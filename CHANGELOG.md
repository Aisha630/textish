# CHANGELOG


## v0.4.0 (2026-04-28)

### Bug Fixes

- :bug: Update CI configuration to install test and lint dependencies; modify test case to ensure
  environment variables inherit correctly
  ([`597d006`](https://github.com/Aisha630/textish/commit/597d006fe55607b037566e755c7a6ff8375cdfa6))

- :bug: Update subprocess environment handling in AppSession to include system environment variables
  ([`30dc626`](https://github.com/Aisha630/textish/commit/30dc626aaeb39861c055a15341a9925c1cdfb1ba))

### Documentation

- :book: Add ARCHITECTURE.md for detailed component design and data flow
  ([`816f393`](https://github.com/Aisha630/textish/commit/816f393c4d7644503e84fb4643c7b303587019b2))

- :book: Clarify exit sequence in AppSession lifecycle management
  ([`1231d74`](https://github.com/Aisha630/textish/commit/1231d74791a21eff1471585a6ec5b154caa89af5))

### Features

- :sparkles: Integrate uvloop for improved asyncio performance in CLI
  ([`8d435df`](https://github.com/Aisha630/textish/commit/8d435dfcdf5bd1fdd57932569c66c1371a9e8106))


## v0.3.0 (2026-04-27)

### Chores

- :wrench: Remove staging branch filter from CI workflow triggers
  ([`2b3ead6`](https://github.com/Aisha630/textish/commit/2b3ead6e680a6e817df560c713047b9728e868d3))

### Continuous Integration

- :construction_worker: Update OS matrix in CI workflow to include ubuntu-latest
  ([`add93c9`](https://github.com/Aisha630/textish/commit/add93c9a04fd635feb6431381f408375ea2e7feb))

### Documentation

- :memo: Add a short demo to the README
  ([`967378c`](https://github.com/Aisha630/textish/commit/967378c09dacbcf6cb0b9e56dd6d9eec06703ed9))

### Features

- :sparkles: Add end-to-end integration tests for textish SSH server
  ([`b13adef`](https://github.com/Aisha630/textish/commit/b13adef1aae7f3a595ed5f44408b9edacef9fd69))

- :sparkles: Refactor the architecture to use PTY-backed app subprocesses
  ([`9742c83`](https://github.com/Aisha630/textish/commit/9742c83c56f59f680aa6a1b6cd5d89c46a3ac21c))

Replace the WebDriver packet protocol bridge with raw PTY forwarding, update the async
  AppConfig-based API, add app environment and authorized_keys support, and refresh tests/docs for
  the new session model.

serve() is now async and accepts AppConfig instead of keyword args. serve_async() and serve_config()
  were removed. app subprocesses now run under a PTY instead of the Textual WebDriver packet
  protocol. textish.protocol was removed. app subprocesses no longer inherit the parent environment;
  pass variables via AppConfig.env or --env.

- :sparkles: Update authorized_keys function to support async file reading and enhance error
  handling in AppSession
  ([`4ec6c79`](https://github.com/Aisha630/textish/commit/4ec6c79f5ab6f5339a00824e8974fee491ad10ee))

### Testing

- :white_check_mark: Improve cleanup verification after client disconnects in SSH server tests
  ([`f58b372`](https://github.com/Aisha630/textish/commit/f58b3726e50f668d873ad56afd0342e2455bec79))


## v0.2.0 (2026-04-12)

### Bug Fixes

- :bug: Add type hints for asyncssh.SSHServerChannel and Mapping in AppSession and
  TextishSSHServerSession
  ([`9c959ee`](https://github.com/Aisha630/textish/commit/9c959ee96b7a9a5a34ca0a8b6b633ada6b7659ac))

- :bug: Validate host_key_path in AppConfig and assert process in AppSession
  ([`f21e8ed`](https://github.com/Aisha630/textish/commit/f21e8ede80bd7722a8c9e26f5024a63d552771da))

### Chores

- :heavy_plus_sign: Add new dependencies for pre-commit and update lock file
  ([`01006c7`](https://github.com/Aisha630/textish/commit/01006c73da5a10a8d0a467f07598fcd788e877f3))

- Update lockfile
  ([`fa4a851`](https://github.com/Aisha630/textish/commit/fa4a851cb7e7f32f7d355107fd7db57ec6bf2895))

### Code Style

- :label: Add type checking with mypy and enhance AppSession and server classes with type hints
  ([`6ae1860`](https://github.com/Aisha630/textish/commit/6ae18604df7c7dbac74eb4d94136a68de1876b2b))

### Documentation

- :memo: Update README with cli information
  ([`5951bdf`](https://github.com/Aisha630/textish/commit/5951bdf7f8f699ecb0891034e0b65216dee3fd17))

### Features

- :sparkles: Implement command-line interface and enhance AppConfig validation
  ([`fb14446`](https://github.com/Aisha630/textish/commit/fb14446dff92783ad30deb246f8723d20bdfa6dd))

- :sparkles: Implement input queue and consumer for ordered data handling in TextishSSHServerSession
  ([`7cbd1cb`](https://github.com/Aisha630/textish/commit/7cbd1cb9a899539ccce79fd8d6103452fb0dab64))

- :sparkles: Introduce SessionManager for managing app session tasks and and allow verbose logging
  ([`7105913`](https://github.com/Aisha630/textish/commit/71059132d416dc3685d604bf44814ac3d9aec963))


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
