# -*- coding: utf-8 -*-
"""DSH RPC API 客户端（std-urllib，零第三方依赖）。

DSH web 服务在回环地址暴露统一 RPC 接口：
  POST /api/<method>
  body: {type:'client-request', rpcId, method, payload}
  resp: {type:'server-response', rpcId, result:{ok:true,value}|{ok:false,error}}

本模块封装常用方法（session.list/create/prompt/history/cancel、host.describe），
并提供事件提取（user/assistant 消息、turn/end、title）与 projections 读取。
"""
import json
import os
import time
import urllib.error
import urllib.request
import uuid


class DshError(Exception):
    pass


class DshClient:
    def __init__(self, base=None, timeout=60):
        self.base = (base or os.environ.get("DSH_WEB_URL") or "http://127.0.0.1:3080").rstrip("/")
        self.timeout = timeout

    # ---- transport ----
    def rpc(self, method, payload=None, timeout=None):
        body = json.dumps({
            "type": "client-request",
            "rpcId": str(uuid.uuid4()),
            "method": method,
            "payload": payload or {},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}/api/{method}", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise DshError(f"无法连接 DSH web（{self.base}）：{e}。请确认 dsh web 正在运行。") from e
        except json.JSONDecodeError as e:
            raise DshError(f"DSH 返回非 JSON：{e}") from e
        result = data.get("result")
        if not result or not result.get("ok"):
            err = (result or {}).get("error") or {}
            raise DshError(f"DSH {method} 失败：{err.get('code')}: {err.get('message')}")
        return result.get("value")

    # ---- sessions ----
    def list_sessions(self):
        """返回 SessionSummary 列表（updatedAt 降序）。"""
        v = self.rpc("session.list", {})
        return v.get("items") or []

    def get_session(self, session_id):
        for s in self.list_sessions():
            if s.get("sessionId") == session_id:
                return s
        return None

    def create_session(self, cwd=None, session_id=None, agent_preset=None):
        payload = {}
        if cwd:
            payload["cwd"] = cwd
        if session_id:
            payload["sessionId"] = session_id
        if agent_preset:
            payload["agentPreset"] = agent_preset
        v = self.rpc("session.create", payload)
        return v.get("sessionId")

    def history(self, session_id, max_messages=50, before_seq=None):
        payload = {"sessionId": session_id, "maxMessages": max_messages}
        if before_seq is not None:
            payload["beforeSeq"] = before_seq
        return self.rpc("session.history", payload)

    def prompt(self, session_id, text, mode="queue"):
        """发送文本到会话（运行 agent）。mode: queue | steer。"""
        return self.rpc("session.prompt", {
            "sessionId": session_id,
            "mode": mode,
            "content": [{"type": "text", "text": text}],
        })

    def cancel(self, session_id):
        return self.rpc("session.cancel", {"sessionId": session_id})

    def rename(self, session_id, title):
        return self.rpc("session.rename", {"sessionId": session_id, "title": title})

    # ---- 回答/取消 pending 提问（ask_user_question）----
    def respond(self, rpc_id, session_id, ok, value=None, error=None):
        """向挂起的提问/审批提交响应（client-response 信封，走 /api/respond）。

        ok=True 时 value 传给 ask_user_question 工具结果；
        ok=False 时以 error（code=cancelled 等）把工具调用置为取消。
        返回服务端回执 {accepted: bool, reason?}。
        """
        result = {"ok": ok}
        if ok:
            result["value"] = value if value is not None else {"sessionId": session_id}
        else:
            result["error"] = error or {
                "code": "cancelled", "message": "cancelled by bridge", "details": {}}
        body = json.dumps({
            "type": "client-response",
            "rpcId": rpc_id,
            "result": result,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}/api/respond", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise DshError(f"提交提问响应失败：{e}") from e

    def answer_question(self, rpc_id, session_id, answers):
        """提交提问答案。answers 形如 {"answers": [{"id","selected":[...],"custom"?}]}。"""
        return self.respond(rpc_id, session_id, True,
                            value={"sessionId": session_id, "answer": answers})

    def cancel_question(self, rpc_id, session_id):
        """把提问置为取消（agent 会收到取消结果继续执行）。"""
        return self.respond(rpc_id, session_id, False,
                            error={"code": "cancelled",
                                   "message": "问题被取消（未收到回答）",
                                   "details": {}})

    def is_running(self, session_id):
        s = self.get_session(session_id)
        return bool(s and s.get("running"))

    def wait_turn_end(self, session_id, timeout=600, poll=3, from_seq=None, keep_waiting=None):
        """等待『from_seq 之后新开始的回合』真正结束，返回 (end_seq, reason)。

        修复要点（勿回退）：
          1) 调用方必须把 from_seq 传进来——否则本函数会匹配到会话里更早的旧回合
             turn/end，在任务还没真正跑完时就提前返回（表现为「任务没完成却推送了
             任务完成」）。
          2) 兜底（未观察到 turn/end）不能仅凭一次「会话未运行」就判定结束：
             DSH 的 running 标志在 agent 各步骤/模型调用间隙可能瞬时为空，
             需连续多轮都未运行才认为回合确实在轮询间隙结束。
          3) 兜底返回的 reason 为 None，由调用方如实上报，绝不假装成功。

        keep_waiting：可调用对象（返回 bool）。当它为真（例如正在等用户回答
        ask_user_question 提问）时，顺延超时截止时间，且不因『会话短暂未运行』
        误判结束——即等用户答完问题、回合真正结束后才返回。

        正确逻辑：
          1) 等到 from_seq 之后出现 turn/start（我们的回合开始）；
          2) 再等到该回合的 turn/end（携带 reason）；
          3) 兜底：回合已开始且会话连续多轮未运行（回合在两次轮询间结束）。
        """
        deadline = time.time() + timeout
        last = from_seq or 0
        saw_start = False
        idle_ticks = 0
        while True:
            if keep_waiting and keep_waiting():
                # 正在等用户回答问题：顺延截止时间，重置空闲计数（避免误判回合结束）
                idle_ticks = 0
                deadline = max(deadline, time.time() + 60)
            elif time.time() >= deadline:
                raise DshError("等待回合结束超时")
            try:
                h = self.history(session_id, max_messages=400)
            except DshError:
                h = {"events": [], "hasMore": False}
            max_seen = last
            ended = None
            events = h.get("events", [])
            has_new_asst = False
            for ent in events:
                e = ent.get("event", {})
                seq = e.get("seq", 0)
                t = e.get("type")
                if seq > max_seen:
                    max_seen = seq
                if seq > (from_seq or 0):
                    if t == "turn/start":
                        saw_start = True
                    elif t == "turn/end" and saw_start:
                        ended = (seq, (e.get("data") or {}).get("reason"))
                    elif t == "assistant/message":
                        has_new_asst = True
            if ended:
                return ended
            last = max_seen
            if not self.is_running(session_id) and (saw_start or has_new_asst):
                if keep_waiting and keep_waiting():
                    idle_ticks = 0
                else:
                    idle_ticks += 1
                    # 连续 2 轮（约 2×poll 秒）都未运行，才认为回合已在轮询间隙结束
                    if idle_ticks >= 2:
                        return last, None
            else:
                idle_ticks = 0
            time.sleep(poll)
        raise DshError("等待回合结束超时")


# ---- 事件提取 ----
def extract_messages(events):
    """从 history events 提取 (seq, role, text, ts)。只看 user/message 与 assistant/message 的 append 面。"""
    out = []
    for ent in events:
        e = ent.get("event", {})
        t = e.get("type")
        data = e.get("data") or {}
        if t == "user/message":
            role = "user"
            content = data.get("content") or []
            text = "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
            if text.strip():
                out.append({"seq": e.get("seq"), "role": role, "text": text, "ts": e.get("time")})
        elif t == "assistant/message":
            msg = data.get("message") or {}
            role = msg.get("role") or "assistant"
            content = msg.get("content") or []
            text = "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
            if text.strip():
                out.append({"seq": e.get("seq"), "role": role, "text": text, "ts": e.get("time")})
    return out


def last_turn_events(events):
    """提取最近一个完整回合内（turn/start..turn/end）的 user/assistant 消息。"""
    msgs = extract_messages(events)
    return msgs[-20:] if msgs else []


def projections_of(history_value):
    """从 history 返回值里取 projections.values。"""
    return ((history_value or {}).get("projections") or {}).get("values") or {}


def session_title(session, history_value=None):
    """从 session 行或 history projections 里取标题。"""
    if session:
        proj = (session.get("projections") or {}).get("values") or {}
        if proj.get("title"):
            return proj["title"]
    if history_value:
        proj = projections_of(history_value)
        if proj.get("title"):
            return proj["title"]
    return None
