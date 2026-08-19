#!/usr/bin/env python3
"""build.py - fold the package into the single files Duum ships as.

Duum is developed as a package because that is how it stays readable, but it
is distributed as one file, twice over:

  dist/desktop/duum.py  Engine + reference rasteriser + tkinter frontend
                        + CLI.  Standard library only; run it directly:
                        `python duum.py mywad.wad`.
  dist/unodos/DUUM.PY   Engine only - the device supplies `uno` natively and
                        a C canvas - so no desktop code ships to it.

They live in separate directories on purpose: Windows filenames are case
insensitive, so duum.py and DUUM.PY are the same file side by side.

The trick that makes this cheap: in a single file there is only one module,
so that module can BE the platform module.  `uno = sys.modules[__name__]`
and every `uno.read_at(...)` in the engine resolves to the host function
defined a few hundred lines above it, unchanged.  The same aliasing covers
`desktop.`, `engine.` and `tkwin.` qualified names, so no call site in the
package has to be rewritten to be bundled.

  python tools/build.py [--check WAD]
"""

import argparse
import ast
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def strip_relative_imports(src, what):
    """Drop `from .x import y` lines; in a single file those names are local.

    Indented ones become `pass` so a suite that held nothing else stays
    syntactically whole.
    """
    out = []
    for line in src.split("\n"):
        s = line.strip()
        if s.startswith("from .") and " import " in s:
            indent = line[:len(line) - len(line.lstrip())]
            if indent:
                out.append(indent + "pass")
            continue
        out.append(line)
    return "\n".join(out)


def section(title, body):
    bar = "# " + "=" * 74
    return "%s\n# %s\n%s\n%s\n" % (bar, title, bar, body)


def banner(kind, version):
    return (
        "# Duum %s - GENERATED FILE, DO NOT EDIT.\n"
        "#\n"
        "# Built from the duum package by tools/build.py on %s.\n"
        "# Edit the package and rebuild; edits here are lost.\n"
        "# Source and licence (MPL-2.0): https://github.com/hmofet/duum\n"
        "#\n"
        "# %s\n"
        % (version, time.strftime("%Y-%m-%d"), kind))


def get_version():
    tree = ast.parse(read("duum", "__init__.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__version__":
                    return node.value.value
    return "0"


def build_desktop(version):
    host = strip_relative_imports(read("duum", "hosts", "desktop.py"), "host")
    raster = strip_relative_imports(read("duum", "raster.py"), "raster")
    engine = strip_relative_imports(read("duum", "engine.py"), "engine")
    front = strip_relative_imports(read("duum", "frontends", "tkwin.py"), "tk")
    cli = strip_relative_imports(read("duum", "__main__.py"), "cli")
    # The version is known here; the commit is not, and must not be, because
    # this file is committed and a hash cannot name the commit containing it.
    # packaging/ stamps the rest into the frozen binary, which is not committed.
    cli = cli.replace('BUILD_VERSION = ""', 'BUILD_VERSION = "%s"' % version, 1)

    alias = (
        "class _Self(object):\n"
        '    """This file, viewed as a module object.\n'
        "\n"
        "    In one file there is one namespace, so the same names the\n"
        "    package reaches through `uno.`, `desktop.`, `engine.` and\n"
        "    `tkwin.` are all just globals here.  Resolving them lazily\n"
        "    through globals() means no call site below had to be rewritten\n"
        "    to be bundled, and unlike sys.modules[__name__] it does not\n"
        '    care how this file was loaded - imported, run, or frozen."""\n'
        "\n"
        "    def __getattr__(self, name):\n"
        "        try:\n"
        "            return globals()[name]\n"
        "        except KeyError:\n"
        "            raise AttributeError(name)\n"
        "\n"
        "\n"
        "uno = desktop = engine = tkwin = _Self()\n")

    return "\n".join([
        banner("Desktop build: engine, rasteriser, tkinter frontend, CLI.",
               version),
        section("host: the platform surface, on top of a plain file", host),
        section("the module is its own platform module", alias),
        section("rasteriser: display list -> pixels (replace this to go fast)",
                raster),
        section("engine: BSP walk, portal clipping, texturing, game logic",
                engine),
        section("frontend: a tkinter window", front),
        section("command line", cli),
    ])


def build_unodos(version):
    engine = read("duum", "engine.py")
    engine = engine.replace(
        "from .hostapi import uno            # ---duum:host-import---",
        "import uno", 1)
    return (banner("UnoDOS build: engine only; the device supplies `uno` and "
                   "a C canvas.", version) + "\n" + engine)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", metavar="WAD",
                    help="render a frame through each built file to prove it")
    args = ap.parse_args()

    version = get_version()
    os.makedirs(DIST, exist_ok=True)
    outs = {}
    for sub, name, src in (("desktop", "duum.py", build_desktop(version)),
                           ("unodos", "DUUM.PY", build_unodos(version))):
        compile(src, name, "exec")            # syntax gate before it lands
        d = os.path.join(DIST, sub)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        outs[name] = p
        print("  %-18s %7d bytes  %5d lines"
              % (sub + "/" + name, len(src.encode()), src.count("\n") + 1))

    if args.check:
        import importlib.util
        spec = importlib.util.spec_from_file_location("duum_dist",
                                                      outs["duum.py"])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.mount(args.check)
        app = mod.Duum()
        cv = mod.Canvas(320, 200)
        app.build(cv)
        if app.err:
            sys.exit("  CHECK FAILED: %s" % app.err)
        app.render()
        app.draw(cv)
        nonblack = sum(1 for b in cv.buf if b)
        if nonblack < len(cv.buf) // 10:
            sys.exit("  CHECK FAILED: frame is essentially blank")
        print("  check              desktop/duum.py rendered a frame "
              "(%d ops, %d%% lit)"
              % (len(app.frame), nonblack * 100 // len(cv.buf)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
