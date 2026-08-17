# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and versions aim to
follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `verify.mjs` bundle-structure check and a GitHub Actions workflow that runs
  it on push and pull requests.
- English README (`README.en.md`).

### Changed
- `package.json` now carries repository / homepage / bugs / keywords metadata.

## [0.3.7] - 2026-08-16

### Fixed
- `_common.ps1` now prefers the Python interpreter named by `DSH_WX_PYTHON`,
  matching the resolution logic in `lib/index.js`.

### Added
- Group-chat whitelist and `@`-mention response policy (`groupWhitelist`,
  `groupMentionOnly`, `myAliases`); fixed message dedup and nickname
  recognition.

## [0.3.x] - 2026-08-15

### Added
- WeChat ⇄ DSH two-way bridge: DSH's `ask_user_question` prompts are forwarded
  to WeChat and answers are relayed back.
- Initial release: DSH WeChat automation plugin (wxauto4) — task progress push,
  message listening, and the two-way bridge.
