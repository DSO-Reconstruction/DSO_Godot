#!/bin/sh
# Build the .crn transcoder.
#
# 2 559 of the client's textures are Crunch-compressed (.crn) and Pillow
# cannot read them. crn_decomp.h is a single-header, public-domain CRN ->
# DXTn transcoder from BinomialLLC/crunch; it is fetched here rather than
# vendored, so this repository stays first-party.
#
#   sh tools/build_crn2dds.sh          # -> build/crn2dds
set -e
here=$(cd "$(dirname "$0")/.." && pwd)
out="$here/build"
mkdir -p "$out"
base=https://raw.githubusercontent.com/BinomialLLC/crunch/master/inc
for h in crn_decomp.h crnlib.h; do
    [ -f "$out/$h" ] || curl -sSfL -o "$out/$h" "$base/$h"
done
# -fpermissive: the 2012 header casts pointers to uint32 inside asserts.
c++ -O2 -w -fno-strict-aliasing -fpermissive -DNDEBUG \
    -I "$out" -o "$out/crn2dds" "$here/tools/crn2dds.cpp"
echo "built $out/crn2dds"
