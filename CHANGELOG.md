# CHANGELOG


## v0.1.0 (2026-04-12)

### Bug Fixes

- :ambulance: Implement fixes for zombie processes, proper cleanup, and early exits
  ([`91ea81a`](https://github.com/Aisha630/textish/commit/91ea81a76f18fcef9f35473ef63db6926d50bdb8))

Fixes #5, #4, #3

- :ambulance: Update host_keys to use Path.expanduser and ensure proper process termination in
  AppSession
  ([`eefd3d9`](https://github.com/Aisha630/textish/commit/eefd3d99905fa2340e744d2e7b4535c5582b686b))

- :bug: Ensure graceful exit of subprocess in AppSession with timeout handling
  ([`4f7188d`](https://github.com/Aisha630/textish/commit/4f7188d849633e4f2b484a1a3638daf7fafbb15f))

- :bug: Improve error handling for WebDriver handshake by adding timeout for stderr reading
  ([`6187a50`](https://github.com/Aisha630/textish/commit/6187a50f03684c16b78ce757905fc0ff2d0b83fb))

- :bug: orrect session_requested method call and add asyncio sleep for terminal size change test
  ([`5b698a0`](https://github.com/Aisha630/textish/commit/5b698a064dff829c179150d714231f909598288f))

### Chores

- :bento: Update the example from a simple Hello app to a simple Chat for a more interactive demo
  ([`22eb3ed`](https://github.com/Aisha630/textish/commit/22eb3ed1d7f02cb9950906cfaa55f4f6cc1d10ea))

- :hammer: Update poetry.lock
  ([`5166a41`](https://github.com/Aisha630/textish/commit/5166a41d514a0d84a860be575f294528c3936a1e))

- :heavy_plus_sign: Add pytest-asyncio'
  ([`26e2485`](https://github.com/Aisha630/textish/commit/26e24855593afc2192453b7bad8584d1b50528c5))

- :recycle: Add a better example in the form of Wordle clone and allow serving from an app class as
  opposed to app command
  ([`33a7a38`](https://github.com/Aisha630/textish/commit/33a7a3867e934a8e461d8759e7c192180bde66ab))

- :wrench: Update .gitignore and .pre-commit-config.yaml for improved dependency management
  ([`c4b32f2`](https://github.com/Aisha630/textish/commit/c4b32f2f06b08d356ff02a1ace9a0759ac77fcfb))

### Code Style

- :art: Format code and improve cleanup
  ([`f7ffd15`](https://github.com/Aisha630/textish/commit/f7ffd158894b6bbd29832e2a329470a04b0a23ce))

### Continuous Integration

- :construction_worker: Add CI/CD workflow to test and publish package Fixes #9
  ([`2312d79`](https://github.com/Aisha630/textish/commit/2312d795af8565710fa1654ff1546702e92287af))

- :construction_worker: Fix dependency installation in CI workflow
  ([`84624ec`](https://github.com/Aisha630/textish/commit/84624ecc8600bec03882232f1b03a5dda7cf5130))

- :construction_worker: Restructure CI workflow to consolidate test jobs and improve clarity
  ([`bb3c3bf`](https://github.com/Aisha630/textish/commit/bb3c3bf0f0a28a0eea7b61346390466a647cd320))

### Documentation

- :memo: Add README.md and LICENSE file
  ([`93918cf`](https://github.com/Aisha630/textish/commit/93918cf367eacb7afee20c30685b8dcfefcf820d))

- :memo: Update README.md to clarify host key requirement and fix license declaration in
  pyproject.toml
  ([`f591bb0`](https://github.com/Aisha630/textish/commit/f591bb03973ef6d95603b2bf6ce2ec8199f18f12))

### Features

- :sparkles: Introduce AppConfig for configurable server settings and enhance process state
  management
  ([`b5643e9`](https://github.com/Aisha630/textish/commit/b5643e9eb846f6eb9ceb1a291e3286a75cafdfc9))

- :sparkles: Set up textish project with SSH server for Textual apps
  ([`761a474`](https://github.com/Aisha630/textish/commit/761a474ba75ca4ee6aa58c3e5b4ea08b6b85af92))

In this commit, I added a basic implementation for SSH server startup for Textual apps using
  `asyncssh`, added AppSession for connection handling and message routing, and defined SSH-Textual
  packet exchange protocol which is basically built on top of the Textual WebDriver. Currently this
  is a basic, but working, setup.

### Refactoring

- :bulb: Enhance documentation and improve code clarity across multiple modules
  ([`dbb3fe9`](https://github.com/Aisha630/textish/commit/dbb3fe91d75479cd3817e9e67c403c21cb617e74))

- :recycle: Add better docstrings, add AppConfig in __all__ and add better error messages
  ([`1b1d36c`](https://github.com/Aisha630/textish/commit/1b1d36cea8054385ce71fbfca47fa2d89a336726))

- :recycle: Simplify AppSession methods and improve test fixtures
  ([`000523c`](https://github.com/Aisha630/textish/commit/000523c12b90f22727615a5efc03574aeabe3450))

### Testing

- :white_check_mark: Add pytest-asyncio dependency and implement unit tests for AppSession and
  protocol.
  ([`5c1dbf8`](https://github.com/Aisha630/textish/commit/5c1dbf816cdacacc812aef14c2ebfc153aefe577))

- :white_check_mark: Enhance test fixtures and improve server connection handling
  ([`19cb81b`](https://github.com/Aisha630/textish/commit/19cb81ba0d179c6d5d10176f5353e986015791e8))

- :white_check_mark: Fix test cases
  ([`ed2f072`](https://github.com/Aisha630/textish/commit/ed2f072aa44c5384ce3f571614c863f76c1cbe6c))
