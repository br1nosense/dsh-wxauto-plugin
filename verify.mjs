#!/usr/bin/env node
/**
 * verify.mjs — DSH bundle 结构校验脚本（供 CI / 本地开发使用）
 *
 * 检查一个 DSH 插件仓库的「可安装性」最小契约：
 *   1. package.json 存在且是合法 JSON
 *   2. 声明了 dsh.bundle.patch，且该文件存在
 *   3. main / exports 引用的模块文件存在（ESM 入口可解析）
 *   4. files 白名单里列出的文件/目录存在
 *   5. lib/*.js 语法检查（node --check 等价）
 *   6. README.md / LICENSE 存在
 *
 * 用法：node verify.mjs [repo-root]   （缺省为当前目录）
 * 退出码：0 = 通过；1 = 失败
 */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const root = resolve(process.argv[2] ?? ".");
const problems = [];
const ok = (msg) => console.log(`  ✓ ${msg}`);
const fail = (msg) => { problems.push(msg); console.error(`  ✗ ${msg}`); };

console.log(`Verifying DSH bundle at ${root}`);

// 1. package.json
const pkgPath = join(root, "package.json");
if (!existsSync(pkgPath)) {
  fail("package.json missing");
  process.exit(1);
}
let pkg;
try {
  pkg = JSON.parse(readFileSync(pkgPath, "utf8"));
  ok("package.json is valid JSON");
} catch (e) {
  fail(`package.json is not valid JSON: ${e.message}`);
  process.exit(1);
}

// 2. dsh.bundle.patch
const bundle = pkg.dsh?.bundle;
if (!bundle?.patch) {
  fail("package.json does not declare dsh.bundle.patch");
} else {
  const patch = join(root, bundle.patch);
  if (existsSync(patch)) ok(`dsh.bundle.patch -> ${bundle.patch} exists`);
  else fail(`dsh.bundle.patch -> ${bundle.patch} missing`);
}

// 3. main / exports entry modules
const entries = [pkg.main];
if (pkg.exports && typeof pkg.exports === "object") {
  for (const [sub, spec] of Object.entries(pkg.exports)) {
    if (sub === "./package.json") continue;
    const file = typeof spec === "string" ? spec : spec?.default ?? spec?.import ?? spec?.require;
    if (typeof file === "string" && !file.endsWith(".json")) entries.push(file);
  }
}
for (const entry of entries) {
  if (!entry) continue;
  const p = join(root, entry);
  if (existsSync(p)) ok(`entry ${entry} exists`);
  else fail(`entry ${entry} missing`);
}

// 4. files whitelist
for (const f of pkg.files ?? []) {
  const p = join(root, f);
  if (existsSync(p)) ok(`files: ${f} exists`);
  else fail(`files: ${f} missing`);
}

// 5. lib/*.js syntax
const libDir = join(root, "lib");
if (existsSync(libDir)) {
  for (const name of readdirSync(libDir).filter((n) => n.endsWith(".js"))) {
    const file = join(libDir, name);
    if (statSync(file).isFile()) {
      const r = spawnSync(process.execPath, ["--check", file], { encoding: "utf8" });
      if (r.status === 0) ok(`syntax: lib/${name}`);
      else fail(`syntax: lib/${name} — ${(r.stderr || "").trim().split("\n").pop()}`);
    }
  }
}

// 6. README / LICENSE
for (const f of ["README.md", "LICENSE"]) {
  if (existsSync(join(root, f))) ok(`${f} exists`);
  else fail(`${f} missing`);
}

if (problems.length) {
  console.error(`\n${problems.length} problem(s) found.`);
  process.exit(1);
}
console.log("\nAll checks passed.");
