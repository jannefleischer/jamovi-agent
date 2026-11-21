# Licensing of jamovi-agent

jamovi-agent consists of two parts with different licenses. Both are free
software, and the two licenses are compatible.

part | folders | license | why
-----|---------|---------|----
server extension | `src/jamovi_agent/`, `shim/` | [AGPL-3.0](LICENSE) | runs inside the jamovi server process and uses its internal modules, which are AGPL-3.0
jamovi module *jamovi-assistant* | `module/jamoviassistant/` | [GPL-2.0-or-later](LICENSE-GPL-2.0) | an R package built on jmvcore (GPL-2.0-or-later), like jamovi's own analyses in jmv
everything else | `ci/`, `scripts/`, `tests/`, `docker_example/`, documentation | [AGPL-3.0](LICENSE) | |

What this means in practice: if you run a modified version of the server
extension as a service for others (e.g. a jamovi server for your
institution), the AGPL requires you to offer them its source code.

jamovi itself is not part of this repository. It is a separate project with
its own licenses (AGPL-3.0 for the server and client, GPL-2.0-or-later for
the analyses), see https://github.com/jamovi/jamovi.
