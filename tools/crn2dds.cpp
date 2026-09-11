// crn -> dds transcoder. Reads "<in>\t<out>" lines on stdin, one per file.
// Built on crn_decomp.h (public domain, BinomialLLC/crunch).
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <iostream>
#define CRND_HEADER_FILE_ONLY
#include "crn_decomp.h"
#undef CRND_HEADER_FILE_ONLY
#include "crn_decomp.h"

static bool read_file(const char* p, std::vector<unsigned char>& out) {
    FILE* f = fopen(p, "rb");
    if (!f) return false;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    out.resize(n);
    bool ok = n == 0 || fread(out.data(), 1, n, f) == (size_t)n;
    fclose(f); return ok;
}

struct DDSHeader { unsigned int v[31]; };

static bool convert(const std::string& in, const std::string& out) {
    std::vector<unsigned char> src;
    if (!read_file(in.c_str(), src) || src.size() < 64) return false;
    crnd::crn_texture_info ti;
    if (!crnd::crnd_get_texture_info(src.data(), (crnd::uint32)src.size(), &ti)) return false;
    unsigned int fourcc = crnd::crnd_crn_format_to_fourcc(ti.m_format);
    crnd::crnd_unpack_context ctx = crnd::crnd_unpack_begin(src.data(), (crnd::uint32)src.size());
    if (!ctx) return false;

    // Mip 0 only: the exporter regenerates mips on the Godot side.
    unsigned int w = ti.m_width, h = ti.m_height;
    unsigned int bx = (w + 3) / 4, by = (h + 3) / 4;
    unsigned int pitch = bx * ti.m_bytes_per_block;
    std::vector<unsigned char> dst(pitch * by);
    void* p = dst.data();
    bool ok = crnd::crnd_unpack_level(ctx, &p, (crnd::uint32)dst.size(), pitch, 0);
    crnd::crnd_unpack_end(ctx);
    if (!ok) return false;

    DDSHeader hd; memset(&hd, 0, sizeof(hd));
    hd.v[0] = 124;                 // dwSize
    hd.v[1] = 0x1 | 0x2 | 0x4 | 0x80000 | 0x1000;  // caps|height|width|linearsize|pixelformat
    hd.v[2] = h; hd.v[3] = w;
    hd.v[4] = (unsigned int)dst.size();
    hd.v[6] = 1;                   // mipmap count
    hd.v[18] = 32;                 // pixelformat size
    hd.v[19] = 0x4;                // DDPF_FOURCC
    hd.v[20] = fourcc;
    hd.v[26] = 0x1000;             // DDSCAPS_TEXTURE
    FILE* f = fopen(out.c_str(), "wb");
    if (!f) return false;
    fwrite("DDS ", 1, 4, f);
    fwrite(&hd, 1, sizeof(hd), f);
    fwrite(dst.data(), 1, dst.size(), f);
    fclose(f);
    return true;
}

int main() {
    std::string line; int ok = 0, bad = 0;
    while (std::getline(std::cin, line)) {
        size_t tab = line.find('\t');
        if (tab == std::string::npos) continue;
        if (convert(line.substr(0, tab), line.substr(tab + 1))) ok++;
        else { bad++; fprintf(stderr, "fail: %s\n", line.substr(0, tab).c_str()); }
    }
    printf("%d ok, %d failed\n", ok, bad);
    return bad ? 1 : 0;
}
