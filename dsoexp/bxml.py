"""Nebula3 binary XML (.bxml / .pbxml) reader.

Layout, little-endian, FourCC packed backwards ('BXML' -> 'LMXB'):
    magic u32, numAttrs u32, numNodes u32, numStrings u32
    numAttrs  x (u32 nameIdx, u32 valueIdx)
    numNodes  x (u32 nameIdx, firstChild, nextSibling, parent, firstAttr, numAttrs)
    numStrings x NUL-terminated UTF-8 strings
Index 0x7fffffff means "none".
"""
import struct

NONE = 0x7FFFFFFF


class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag):
        self.tag, self.attrs, self.children = tag, {}, []

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()


def parse(data):
    if data[:4] != b"LMXB":
        raise ValueError("not a BXML file")
    n_attr, n_node, n_str = struct.unpack_from("<3I", data, 4)
    off = 16
    attrs = struct.unpack_from("<%dI" % (n_attr * 2), data, off)
    off += n_attr * 8
    nodes = struct.unpack_from("<%dI" % (n_node * 6), data, off)
    off += n_node * 24
    raw = data[off:].split(b"\x00")[:n_str]
    if len(raw) != n_str:
        raise ValueError("string table short: %d/%d" % (len(raw), n_str))
    S = [s.decode("utf-8", "replace") for s in raw]

    out = []
    for i in range(n_node):
        name, child, sib, parent, first, count = nodes[i * 6:i * 6 + 6]
        nd = Node(S[name])
        for a in range(first, first + count):
            nd.attrs[S[attrs[a * 2]]] = S[attrs[a * 2 + 1]]
        out.append(nd)
    root = None
    for i in range(n_node):
        name, child, sib, parent, first, count = nodes[i * 6:i * 6 + 6]
        if parent == NONE:
            root = out[i]
        # children come as firstChild + nextSibling chain, in order
        c = child
        while c != NONE:
            out[i].children.append(out[c])
            c = nodes[c * 6 + 2]
    if root is None:
        raise ValueError("no root node")
    return root


_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}


def _esc(s):
    return "".join(_ESC.get(c, c) for c in s)


def to_xml(node, indent=0):
    pad = "  " * indent
    a = "".join(' %s="%s"' % (k, _esc(v)) for k, v in node.attrs.items())
    if not node.children:
        return "%s<%s%s/>\n" % (pad, node.tag, a)
    return ("%s<%s%s>\n" % (pad, node.tag, a)
            + "".join(to_xml(c, indent + 1) for c in node.children)
            + "%s</%s>\n" % (pad, node.tag))


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        sys.stdout.write(to_xml(parse(open(p, "rb").read())))
