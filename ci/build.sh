#!/bin/sh
# Build the release artifacts into dist/:
#   jamoviassistant_<version>.jmo   jamovi module (chat UI), built with jmc
#   jamovi-agent-<version>.tar.gz   server extension (src/jamovi_agent + shim)
#
# Runs in rocker/r-ver:<R version of the target jamovi> (CI) or any Debian/Ubuntu
# image with R; installs node, the jamovi compiler (from the jamovi repo) and
# jmvcore. VERSION defaults to the version in pyproject.toml.
set -eu
cd "$(dirname "$0")/.."
JAMOVI_REF="${JAMOVI_REF:-v28.14}"   # jamovi git ref that provides jamovi-compiler
JAMOVI_APP_VERSION="${JAMOVI_APP_VERSION:-28.18.0}"   # target jamovi version
VERSION="${VERSION:-$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)}"
WORK="${WORK:-/tmp/jamovi-agent-build}"
mkdir -p dist "$WORK"

if ! command -v jmc >/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    git nodejs npm ca-certificates >/dev/null
  git clone -q --depth 1 --branch "$JAMOVI_REF" --filter=blob:none --sparse \
    https://github.com/jamovi/jamovi.git "$WORK/jamovi"
  git -C "$WORK/jamovi" sparse-checkout set jamovi-compiler
  (cd "$WORK/jamovi/jamovi-compiler" && npm install --silent && npm install -g --silent .)
fi

# jmvcore and R6 are provided by jamovi at runtime (--skip-deps), but jmc
# needs them to install the package while building. Default repos of the
# image (rocker: Posit binaries, much faster than source packages).
RLIBS="$WORK/rlib"
mkdir -p "$RLIBS"
Rscript -e "if (!requireNamespace('jmvcore', quietly=TRUE)) install.packages(c('jmvcore','R6'), lib='$RLIBS')"

# jmc looks for a jamovi installation even when the version is given; outside
# a jamovi image a stub is enough.
if [ ! -e /usr/lib/jamovi/bin/jamovi ]; then
  mkdir -p /usr/lib/jamovi/bin && printf '#!/bin/sh\n' > /usr/lib/jamovi/bin/jamovi
  chmod +x /usr/lib/jamovi/bin/jamovi
fi

rm -rf "$WORK/module" && cp -r module/jamoviassistant "$WORK/module"
(cd "$WORK/module" && jmc --build . --rhome "$(R RHOME)" --rlibs "$RLIBS" --skip-deps \
   --assume-app-version "$JAMOVI_APP_VERSION")
cp "$WORK"/module/jamoviassistant_*.jmo dist/

tar -czf "dist/jamovi-agent-$VERSION.tar.gz" --transform "s#^#jamovi-agent-$VERSION/#" \
  src/jamovi_agent shim/extras.py LICENSE LICENSE.md README.md
(cd dist && sha256sum * > SHA256SUMS)
ls -la dist
