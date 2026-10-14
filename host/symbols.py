"""
Builds a variable map (name -> address, size, signedness) from the DWARF debug
info that GCC puts in the library with -g. Works the same for a Linux .so and
a Windows (MinGW) .dll, and includes file-static variables.

Result is cached as <library>.symbols.json next to the library so the
simulator itself doesn't need objdump at runtime.

    python symbols.py ../build/fridge_sil.dll      # (re)generate + print
"""
import json
import os
import re
import shutil
import subprocess
import sys

_DIE = re.compile(r"^\s*<(\d+)><([0-9a-f]+)>: Abbrev Number: \d+ \((DW_TAG_\w+)\)")
_ATTR = re.compile(r"^\s*<[0-9a-f]+>\s+(DW_AT_\w+)\s*:\s*(.*)$")
_REF = re.compile(r"<0x([0-9a-f]+)>")
_ADDR = re.compile(r"DW_OP_addr: ([0-9a-f]+)")

SCALAR_TAGS = {"DW_TAG_base_type", "DW_TAG_enumeration_type", "DW_TAG_pointer_type"}
PASS_TAGS = {"DW_TAG_typedef", "DW_TAG_volatile_type", "DW_TAG_const_type",
             "DW_TAG_restrict_type", "DW_TAG_atomic_type"}


def find_objdump():
    for name in ("objdump", "x86_64-w64-mingw32-objdump", "objdump.exe"):
        p = shutil.which(name)
        if p:
            return p
    gcc = shutil.which("gcc")                       # MinGW: objdump sits next to gcc
    if gcc:
        for name in ("objdump.exe", "objdump"):
            p = os.path.join(os.path.dirname(gcc), name)
            if os.path.exists(p):
                return p
    raise FileNotFoundError(
        "objdump not found. It ships with MinGW-w64 GCC (w64devkit / MSYS2); "
        "make sure the folder with gcc.exe is on PATH.")


def _value(raw):
    """'(indirect string, offset: 0x1d3): g_ms' -> 'g_ms'; '2' -> '2'"""
    raw = raw.strip()
    if raw.startswith("("):
        raw = raw.split("): ", 1)[-1]
    return raw


def parse_dwarf(text):
    dies, cur = {}, None
    for line in text.splitlines():
        m = _DIE.match(line)
        if m:
            cur = {"tag": m.group(3)}
            dies[int(m.group(2), 16)] = cur
            continue
        if cur is None:
            continue
        m = _ATTR.match(line)
        if not m:
            continue
        at, val = m.group(1), m.group(2)
        if at in ("DW_AT_type", "DW_AT_specification", "DW_AT_abstract_origin"):
            r = _REF.search(val)
            if r:
                cur[at] = int(r.group(1), 16)
        elif at == "DW_AT_location":
            a = _ADDR.search(val)
            if a:
                cur["addr"] = int(a.group(1), 16)
        elif at in ("DW_AT_name", "DW_AT_byte_size", "DW_AT_encoding"):
            cur[at] = _value(val)
    return dies


def _resolve_type(dies, ref, depth=0):
    """-> (size, kind, type_name) where kind is 'i' signed, 'u' unsigned, 'f' float, or None"""
    die = dies.get(ref)
    if die is None or depth > 20:
        return None, None, "?"
    tag = die["tag"]
    if tag in PASS_TAGS:
        size, kind, base = _resolve_type(dies, die.get("DW_AT_type"), depth + 1)
        name = die.get("DW_AT_name", base) if tag == "DW_TAG_typedef" else base
        return size, kind, name
    if tag not in SCALAR_TAGS:
        size = die.get("DW_AT_byte_size")
        return (int(size, 0) if size else None), None, tag.replace("DW_TAG_", "")
    size = int(die.get("DW_AT_byte_size", "8"), 0)
    if tag == "DW_TAG_pointer_type":
        return size, "u", "pointer"
    if tag == "DW_TAG_enumeration_type":
        _, kind, _ = _resolve_type(dies, die.get("DW_AT_type"), depth + 1) \
            if "DW_AT_type" in die else (None, "u", None)
        return size, kind or "u", "enum " + die.get("DW_AT_name", "")
    enc = die.get("DW_AT_encoding", "").split()[0] if die.get("DW_AT_encoding") else ""
    kind = {"5": "i", "6": "i", "4": "f"}.get(enc, "u")   # 5 signed, 6 signed char, 4 float
    return size, kind, die.get("DW_AT_name", "?")


def extract(lib_path, objdump=None):
    text = subprocess.run([objdump or find_objdump(), "--dwarf=info", lib_path],
                          capture_output=True, text=True, check=True).stdout
    dies = parse_dwarf(text)
    out = {}
    for d in dies.values():
        if d["tag"] != "DW_TAG_variable" or "addr" not in d:
            continue
        src = dies.get(d.get("DW_AT_specification")) or dies.get(d.get("DW_AT_abstract_origin")) or {}
        name = d.get("DW_AT_name") or src.get("DW_AT_name")
        tref = d.get("DW_AT_type") or src.get("DW_AT_type")
        if not name:
            continue
        size, kind, tname = _resolve_type(dies, tref)
        out[name] = {"addr": d["addr"], "size": size, "kind": kind, "type": tname}
    if "sim_anchor" not in out:
        raise RuntimeError("no debug info found: build the library with -g and don't strip it")
    return out


def load(lib_path):
    """Cached variable map for lib_path (regenerated if the library is newer)."""
    cache = lib_path + ".symbols.json"
    if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(lib_path):
        with open(cache) as f:
            return json.load(f)
    syms = extract(lib_path)
    with open(cache, "w") as f:
        json.dump(syms, f, indent=1, sort_keys=True)
    return syms


if __name__ == "__main__":
    lib = sys.argv[1]
    cache = lib + ".symbols.json"
    if os.path.exists(cache):
        os.remove(cache)
    syms = load(lib)
    fw = {k: v for k, v in syms.items() if not k.startswith("sim_")}
    print(f"wrote {cache}: {len(syms)} variables ({len(fw)} firmware)")
