#!/usr/bin/env bash
# Compare each script changed by ha-mcp#2937 at the merge base (B) and the PR head (H).
set -uo pipefail
BASE=2556e9fe2240be3e7d1908a50d7f8478870ed2ba
HEAD=d32ce82423d4be267372a8defb44bc1a81a7b75a
LIVE=$(cd "$(dirname "$0")" && pwd)
W=$RUNNER_TEMP/w
mkdir -p "$W" && cd "$W"
RES=$W/results
: >"$RES"
pass() { echo "PASS: $*" | tee -a "$RES"; }
fail() { echo "FAIL: $*" | tee -a "$RES"; }
info() { echo "INFO: $*" | tee -a "$RES"; }
same() { if cmp -s "$2" "$3"; then pass "$1"; else fail "$1"; diff -u "$2" "$3" | head -60; fi; }
SCRIPTS="build_mirror_release_notes check_ha_constraint_alignment codeql_quality_gate extract_tools generate_star_history module_size_ratchet translate_locales webhook_proxy_sync"

echo "::group::setup"
git clone -q https://github.com/homeassistant-ai/ha-mcp repo
git -C repo fetch -q --tags origin
git -C repo fetch -q origin pull/2937/head
git -C repo cat-file -e "$HEAD" || { echo "PR head $HEAD not found"; exit 1; }
tree() { mkdir -p "$1" && git -C "$W/repo" archive "$HEAD" | tar -x -C "$1"; }
swap_base() { for s in $SCRIPTS; do git -C "$W/repo" show "$BASE:scripts/$s.py" >"$1/scripts/$s.py"; done; }
tree treeH
tree treeB && swap_base treeB
mkdir -p base_scripts && for s in $SCRIPTS; do git -C repo show "$BASE:scripts/$s.py" >"base_scripts/$s.py"; done
(cd treeH && uv sync --group dev -q)
export PATH="$W/treeH/.venv/bin:$PATH"
PY=$W/treeH/.venv/bin/python
$PY --version
echo "::endgroup::"

# ---------------------------------------------------------------- 1 release notes
echo "::group::build_mirror_release_notes"
cd "$W/repo"
for t in $(git tag -l 'v*.dev*' --sort=-v:refname | head -4); do
  pin=${t#v}
  src=$(git rev-parse "$t^")
  for s in B H; do
    $PY "$W/tree$s/scripts/build_mirror_release_notes.py" --repo-dir . --dev-pin "$pin" \
      --dist ha-mcp-dev --source "$src" --source-label "homeassistant-ai/ha-mcp@${src:0:7}" \
      --out "$W/rn_${s}_$pin.md" 2>"$W/rn_${s}_$pin.err"
    echo $? >"$W/rn_${s}_$pin.rc"
  done
  same "release notes --dev-pin $pin: same exit code ($(cat "$W/rn_H_$pin.rc"))" "$W/rn_B_$pin.rc" "$W/rn_H_$pin.rc"
  same "release notes --dev-pin $pin: identical notes ($(grep -c '^- ' "$W/rn_H_$pin.md" || true) entries)" "$W/rn_B_$pin.md" "$W/rn_H_$pin.md"
done
for t in "" $(git tag -l 'v[0-9]*' --sort=-v:refname | grep -Ev 'dev' | head -3); do
  label=${t:-default-newest}
  for s in B H; do
    $PY "$W/tree$s/scripts/build_mirror_release_notes.py" --repo-dir . ${t:+--tag "$t"} \
      --out "$W/rs_${s}_$label.md" 2>"$W/rs_${s}_$label.err"
    echo $? >"$W/rs_${s}_$label.rc"
  done
  same "release notes stable $label: same exit code ($(cat "$W/rs_H_$label.rc"))" "$W/rs_B_$label.rc" "$W/rs_H_$label.rc"
  same "release notes stable $label: identical notes" "$W/rs_B_$label.md" "$W/rs_H_$label.md"
done
for opt in "" "-O"; do
  for s in B H; do
    rm -f "$W/np_$s.md"
    $PY $opt "$W/tree$s/scripts/build_mirror_release_notes.py" --repo-dir . --dev-pin 9.0.0 \
      --out "$W/np_$s.md" >/dev/null 2>"$W/np_$s.err"
    echo $? >"$W/np_$s.rc"
    info "non-dev pin ${opt:-(no -O)} side $s: rc=$(cat "$W/np_$s.rc"), file=$([ -e "$W/np_$s.md" ] && echo written || echo none), stderr: $(tail -1 "$W/np_$s.err")"
  done
  if [ "$(cat "$W/np_H.rc")" = 1 ] && [ ! -e "$W/np_H.md" ] && grep -q 'not a dev build version: 9.0.0' "$W/np_H.err"; then
    pass "non-dev pin ${opt:-(no -O)}: head exits 1 with the ValueError message and writes no file"
  else fail "non-dev pin ${opt:-(no -O)}: head"; fi
done
echo "::endgroup::"

# ---------------------------------------------------------------- 2 star history
echo "::group::generate_star_history"
for s in B H; do
  (cd "$W/tree$s" && $PY scripts/generate_star_history.py --repository homeassistant-ai/ha-mcp \
    --output-dir "$W/sh_$s") >"$W/sh_$s.out" 2>&1
  echo $? >"$W/sh_$s.rc"
done
same "star history: same exit code ($(cat "$W/sh_H.rc"))" "$W/sh_B.rc" "$W/sh_H.rc"
same "star history: identical light.svg" "$W/sh_B/light.svg" "$W/sh_H/light.svg"
same "star history: identical dark.svg" "$W/sh_B/dark.svg" "$W/sh_H/dark.svg"
info "star history desc: $(grep -o '<desc>[^<]*' "$W/sh_H/light.svg" | head -1)"
for s in B H; do
  $PY "$W/tree$s/scripts/generate_star_history.py" --input-json x.json >/dev/null 2>"$W/shf_$s.err"
  info "star history --input-json side $s: rc=$? $(tail -1 "$W/shf_$s.err")"
done
echo "::endgroup::"

# ---------------------------------------------------------------- 3 webhook proxy sync
echo "::group::webhook_proxy_sync"
SYNC='import importlib.util,sys,pathlib
spec=importlib.util.spec_from_file_location("webhook_proxy_sync",sys.argv[1]);m=importlib.util.module_from_spec(spec)
sys.modules["webhook_proxy_sync"]=m;spec.loader.exec_module(m)
print(m.sync(sys.argv[2],None if sys.argv[3]=="-" else sys.argv[3],root=pathlib.Path(sys.argv[4])))'
for case in "reset -" "promote patch" "promote minor"; do
  set -- $case
  tag="$1-$2"
  rm -rf "$W/wp_B" "$W/wp_H" "$W/wp_0"
  tree "$W/wp_B"; tree "$W/wp_H"; tree "$W/wp_0"
  vB=$($PY -c "$SYNC" "$W/base_scripts/webhook_proxy_sync.py" "$1" "$2" "$W/wp_B" 2>"$W/wp_B.err"); rB=$?
  vH=$($PY -c "$SYNC" "$W/treeH/scripts/webhook_proxy_sync.py" "$1" "$2" "$W/wp_H" 2>"$W/wp_H.err"); rH=$?
  [ "$rB$rH" = 00 ] || { fail "webhook sync $tag: rc B=$rB H=$rH"; cat "$W/wp_B.err" "$W/wp_H.err"; continue; }
  [ "$vB" = "$vH" ] && pass "webhook sync $tag: same version ($vH)" || fail "webhook sync $tag: version B=$vB H=$vH"
  changed=$(diff -rq -x .ruff_cache "$W/wp_0" "$W/wp_H" | wc -l)
  if diff -r -x .ruff_cache "$W/wp_B" "$W/wp_H" >"$W/wp.diff"; then
    pass "webhook sync $tag: byte-identical trees ($changed files changed by the sync)"
  else fail "webhook sync $tag: trees differ"; head -60 "$W/wp.diff"; fi
done
echo "::endgroup::"

# ---------------------------------------------------------------- 4 module size ratchet
echo "::group::module_size_ratchet"
git -C "$W/repo" worktree add -q --detach "$W/msr" "$HEAD"
MSR='import importlib.util,sys,pathlib
spec=importlib.util.spec_from_file_location("module_size_ratchet",sys.argv[1]);m=importlib.util.module_from_spec(spec)
sys.modules["module_size_ratchet"]=m;spec.loader.exec_module(m)
sys.exit(m.main(sys.argv[3:],repo_root=pathlib.Path(sys.argv[2])))'
msr_case() {
  for s in B H; do
    f=$W/base_scripts/module_size_ratchet.py; [ $s = H ] && f=$W/msr/scripts/module_size_ratchet.py
    $PY -c "$MSR" "$f" "$W/msr" "$@" >"$W/msr_$s.out" 2>&1; echo "rc=$?" >>"$W/msr_$s.out"
  done
  same "module_size_ratchet $label $*: same output and exit ($(tail -1 "$W/msr_H.out"))" "$W/msr_B.out" "$W/msr_H.out"
}
label="clean tree"
msr_case --check; msr_case --staged --check; msr_case --base "$BASE" --check
# A staged file grown past its limit, and the same growth left unstaged.
big=$(cd "$W/msr" && git ls-files 'src/*.py' | head -1)
for i in $(seq 1100); do echo "x_$i = $i"; done >>"$W/msr/$big"
label="unstaged growth of $big"; msr_case --check; msr_case --staged --check
git -C "$W/msr" add "$big"
label="staged growth of $big"; msr_case --check; msr_case --staged --check
git -C "$W/msr" -c user.name=t -c user.email=t@t commit -qm grow
label="committed growth vs base"; msr_case --base "$BASE" --check
echo "::endgroup::"

# ---------------------------------------------------------------- 5 extract_tools
echo "::group::extract_tools"
for s in B H; do
  rm -rf "$W/et_$s"; tree "$W/et_$s"
  [ $s = B ] && cp "$W/base_scripts/extract_tools.py" "$W/et_B/scripts/"
  (cd "$W/et_$s" && $PY scripts/extract_tools.py --check) >"$W/etc_$s.out" 2>&1; echo "rc=$?" >>"$W/etc_$s.out"
done
same "extract_tools --check: same output and exit ($(tail -1 "$W/etc_H.out"))" "$W/etc_B.out" "$W/etc_H.out"
for s in B H; do
  rm -rf "$W/etw_$s"; tree "$W/etw_$s"
  [ $s = B ] && cp "$W/base_scripts/extract_tools.py" "$W/etw_B/scripts/"
  # Make the generated outputs stale so the write mode has work to do.
  sed -i 's/tools-[^-]*-blue/tools-0-blue/' "$W/etw_$s/README.md"
  (cd "$W/etw_$s" && $PY scripts/extract_tools.py && $PY scripts/extract_tools.py --check) >"$W/etw_$s.out" 2>&1; echo "rc=$?" >>"$W/etw_$s.out"
done
diff -rq "$W/etw_B" "$W/etw_H" | grep -v 'scripts/extract_tools.py' >"$W/etw.diff"
[ -s "$W/etw.diff" ] && { fail "extract_tools write mode: generated files differ"; cat "$W/etw.diff"; } || pass "extract_tools write mode: identical generated files, then --check passes ($(tail -1 "$W/etw_H.out"))"
for s in B H; do
  rm -rf "$W/etm_$s"; tree "$W/etm_$s"
  [ $s = B ] && cp "$W/base_scripts/extract_tools.py" "$W/etm_B/scripts/"
  # Markers still present, but a <summary> line now sits between <details> and the start marker.
  sed -i '/<!-- TOOLS_TABLE_START -->/i <summary>Tools</summary>' "$W/etm_$s/README.md"
  (cd "$W/etm_$s" && $PY scripts/extract_tools.py --check) >"$W/etm_$s.out" 2>&1; echo "rc=$?" >>"$W/etm_$s.out"
  info "extract_tools markers not wrapped, side $s: $(tr '\n' ' ' <"$W/etm_$s.out" | tail -c 400)"
done
grep -q 'rc=1' "$W/etm_H.out" && grep -q 'directly inside' "$W/etm_H.out" && pass "extract_tools unwrapped markers: head fails --check with the <details> message" || fail "extract_tools unwrapped markers"
echo "::endgroup::"

# ---------------------------------------------------------------- 6 codeql allowlist
echo "::group::codeql_quality_gate"
$PY - "$W/base_scripts/codeql_quality_gate.py" "$W/treeH/scripts/codeql_quality_gate.py" <<'EOF' && pass "codeql ALLOWLIST: equal at base and head" || fail "codeql ALLOWLIST differs"
import importlib.util, sys
def load(p, n):
    s = importlib.util.spec_from_file_location(n, p); m = importlib.util.module_from_spec(s); sys.modules[n] = m; s.loader.exec_module(m); return m
a, b = load(sys.argv[1], "qa").ALLOWLIST, load(sys.argv[2], "qb").ALLOWLIST
print(len(a), len(b)); sys.exit(a != b)
EOF
echo "::endgroup::"

# ---------------------------------------------------------------- 7 HA constraint alignment
echo "::group::check_ha_constraint_alignment"
HAV=$(grep -oE 'home-assistant:[0-9.]+' "$W/treeH/.github/workflows/pr.yml" | head -1 | cut -d: -f2)
for v in "$HAV" 1999.1.0; do
  for s in B H; do
    (cd "$W/tree$s" && $PY scripts/check_ha_constraint_alignment.py --ha-version "$v") >"$W/ca_$s.out" 2>&1; echo "rc=$?" >>"$W/ca_$s.out"
  done
  same "constraint alignment --ha-version $v: same output and exit ($(tail -1 "$W/ca_H.out"))" "$W/ca_B.out" "$W/ca_H.out"
done
CA='import importlib.util,sys,time
spec=importlib.util.spec_from_file_location("ca",sys.argv[1]);m=importlib.util.module_from_spec(spec);sys.modules["ca"]=m;spec.loader.exec_module(m)
m._CONSTRAINTS_URL="http://127.0.0.1:9/{version}";t=time.monotonic();rc=m.main(["--ha-version",sys.argv[2]])
print(f"rc={rc} elapsed={time.monotonic()-t:.0f}s")'
for s in B H; do
  r=$($PY -c "$CA" "$W/tree$s/scripts/check_ha_constraint_alignment.py" "$HAV" 2>/dev/null | tail -1); eval "ca_$s='$r'"
  info "constraint alignment unreachable host, side $s: $r"
done
[ "$ca_B" = "$ca_H" ] && pass "constraint alignment unreachable host: same exit and backoff ($ca_H)" || fail "constraint alignment unreachable host: B=$ca_B H=$ca_H"
echo "::endgroup::"

# ---------------------------------------------------------------- 8 translate_locales
echo "::group::translate_locales"
for s in B H; do
  (cd "$W/tree$s" && $PY scripts/translate_locales.py --dry-run) >"$W/tld_$s.out" 2>&1; echo "rc=$?" >>"$W/tld_$s.out"
done
same "translate_locales --dry-run: same plan and exit ($(head -1 "$W/tld_H.out"))" "$W/tld_B.out" "$W/tld_H.out"
port=8800
tl_run() {  # scenario stub-mode delay -> runs both sides in parallel against their own stub
  local sc=$1 mode=$2 delay=$3 pids=""
  for s in B H; do
    port=$((port + 1)); : >"$W/stub_${sc}_$s.log"
    $PY "$LIVE/stub.py" $port "$mode" "$W/stub_${sc}_$s.log" "$delay" & echo $! >"$W/stubpid_$s"
    sleep 1
    (cd "$W/tree$s" && $PY "$LIVE/tl_driver.py" "$W/tree$s/scripts/translate_locales.py" "$sc" $port) >"$W/tl_${sc}_$s.json" 2>"$W/tl_${sc}_$s.err" &
    pids="$pids $!"
  done
  wait $pids
  for s in B H; do kill "$(cat "$W/stubpid_$s")" 2>/dev/null
    gaps=$(awk 'NR>1{printf "%.1f ", $1-p} {p=$1}' "$W/stub_${sc}_$s.log")
    info "translate $sc side $s: requests=$(wc -l <"$W/stub_${sc}_$s.log") gaps=[${gaps}] $(cat "$W/tl_${sc}_$s.json")"
  done
}
jq_() { $PY -c "import json,sys;d=json.load(open(sys.argv[1]));print(eval(sys.argv[2]))" "$@"; }
tl_run call 503 0
reqH=$(wc -l <"$W/stub_call_H.log"); eH=$(jq_ "$W/tl_call_H.json" 'd["elapsed"]'); eB=$(jq_ "$W/tl_call_B.json" 'd["elapsed"]')
$PY -c "import sys; sys.exit(not ($reqH==5 and 195<=$eH<=215 and 295<=$eB<=315))" \
  && pass "translate _call_gemini always-503: 5 attempts, head gives up after ${eH}s (base ${eB}s, the 100 s trailing sleep is gone)" \
  || fail "translate _call_gemini always-503: head reqs=$reqH elapsed=$eH, base elapsed=$eB"
tl_run surface-400 400 0
gH=$(awk 'NR==2{print $1-p} {p=$1}' "$W/stub_surface-400_H.log"); gB=$(awk 'NR==2{print $1-p} {p=$1}' "$W/stub_surface-400_B.log")
$PY -c "import sys; sys.exit(not (6.5<=$gH<=8.5 and $gB<1.5))" \
  && pass "translate failed chunk (HTTP 400): head waits ${gH}s before the next chunk request (base ${gB}s)" \
  || fail "translate failed chunk: gap head=$gH base=$gB"
eH=$(jq_ "$W/tl_surface-400_H.json" 'd["elapsed"]')
$PY -c "import sys; sys.exit(not ($eH<9.5))" && pass "translate give-up after 2nd failure: no wait follows it (head total ${eH}s)" || fail "translate give-up wait: head total $eH"
tl_run surface-ok ok 0
jq_ "$W/tl_surface-ok_B.json" 'sorted(d["results"].items())' >"$W/tlok_B"; jq_ "$W/tl_surface-ok_H.json" 'sorted(d["results"].items())' >"$W/tlok_H"
same "translate answered chunks: same translations kept" "$W/tlok_B" "$W/tlok_H"
same "translate answered chunks: same request count" <(wc -l <"$W/stub_surface-ok_B.log") <(wc -l <"$W/stub_surface-ok_H.log")
$PY -c "import sys; g=[float(x) for x in '''$(awk 'NR>1{print $1-p} {p=$1}' "$W/stub_surface-ok_H.log")'''.split()]; sys.exit(not (len(g)==2 and all(6.5<=x<=8.5 for x in g)))" \
  && pass "translate answered chunks: head paces each request 7 s apart" || fail "translate answered chunks: head gaps"
tl_run late-empty empty 2
eH=$(jq_ "$W/tl_late-empty_H.json" 'd["elapsed"]'); eB=$(jq_ "$W/tl_late-empty_B.json" 'd["elapsed"]')
rH=$(wc -l <"$W/stub_late-empty_H.log")
$PY -c "import sys; sys.exit(not ($rH==1 and $eH<4))" && jq_ "$W/tl_late-empty_H.json" 'd["failures"][0]' | grep -q 'time budget exhausted; not retrying' \
  && pass "translate rejected answer past the deadline: head fails it at once with the budget note, 1 request, ${eH}s (base ${eB}s)" \
  || fail "translate late rejected answer: head reqs=$rH elapsed=$eH base=$eB"
echo "::endgroup::"

echo; echo "================ SUMMARY"; cat "$RES" | tee -a "$GITHUB_STEP_SUMMARY"
! grep -q '^FAIL' "$RES"
