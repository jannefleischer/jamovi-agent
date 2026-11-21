# Minimal jamovi image with jamovi-agent. Build context: the repository root.
#   ci/build.sh                      # creates dist/ (or download a release)
#   docker compose -f docker_example/compose.yaml up --build
ARG JAMOVI_IMAGE=jamovi/jamovi:28.18
FROM ${JAMOVI_IMAGE}

# Path mode (jamovi under /jamovi/, analyses under /jamovi/a/, results under
# /jamovi/r/) needs relative asset paths in the client; jamovi builds them
# absolute. Same patches as in the ILS JupyterHub image.
RUN cd /usr/lib/jamovi/client \
 && sed -i 's#"/assets/#"../assets/#g' index.html analysisui.html \
 && sed -i 's#"/config.js"#"../config.js"#' index.html \
 && sed -i 's#"/assets/#"../../assets/#g' resultsview.html \
 && cd assets \
 && sed -i 's#url(/assets/#url(./#g' *.css \
 && sed -i 's#fetch(`/version`)#fetch(`../version`)#' main-*.js \
 && sed -i 's#`${window.location.origin}/${#`../${#g' main-*.js

# server extension: package next to jamovi's server code + the hook module
COPY src/jamovi_agent /usr/lib/jamovi/server/jamovi_agent
COPY shim/extras.py   /usr/lib/jamovi/server/jamovi/server/extras.py

# chat UI as jamovi module (system module: preinstalled for everyone)
COPY dist/jamoviassistant_*.jmo /tmp/
RUN unzip -q /tmp/jamoviassistant_*.jmo -d /usr/lib/jamovi/modules \
 && rm /tmp/jamoviassistant_*.jmo \
 && test -f /usr/lib/jamovi/modules/jamoviassistant/jamovi.yaml
