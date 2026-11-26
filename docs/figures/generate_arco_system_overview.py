"""Draw the ARCO overview with editable workflow, policy, and engine components."""

import base64
import ctypes
import ctypes.util
from functools import lru_cache
from html import escape
from pathlib import Path

import cairosvg
from PIL import ImageFont

from generate_icml_figures import Art, OUTPUT, PowerPointExporter


W, H = 760, 402
FONTS = Path(__file__).parent / "assets/comic_neue"
INK, PAPER, GRAY = "#18232c", "#ffffff", "#52606a"
BLUE, SKY, ICE = "#1d65ad", "#72c1f1", "#eaf6fe"
GREEN, MINT, LEAF = "#237452", "#abe2bd", "#eef9ed"
RUST, GOLD, CREAM = "#a45420", "#ffc26e", "#fff4df"
PURPLE, LILAC = "#704aa0", "#f3ecfc"
CAPTION = (
    "ARCO observes dynamic agent dependencies and the SGLang cache state. "
    "D1 charges the first root and child prefills against one effective HBM and Host budget, "
    "protecting live Host-only prefixes and reserving capacity for existing work. Among fitting "
    "candidates a child blocking CALL or ALL-JOIN has priority. Permits are rechecked at native prefill. "
    "D2 asynchronously backs up the parent's missing stable extents on verified SPAWN or WAIT_JOIN. "
    "After ACK a blocked parent's backed device pages may be released for a real HBM deficit. "
    "The last outstanding child's valid final EOS can start H2D for missing parent KV. The parent "
    "runs after actual JOIN, submission and native checks. D3 pairs a tool-blocked donor with a ready "
    "request needing HBM when the predicted remaining wait is likely to cover migration costs. "
    "Prediction and the periodic two-second working-set check share a native planning boundary. "
    "After writeback ACK the runtime rechecks tool state and the live deficit before release. "
    "SGLang owns physical pages, transfers and GPU admission. The panels show selected moments "
    "in a workflow. Page counts and timing are illustrative."
)


@lru_cache(None)
def font(size, bold):
    return ImageFont.truetype(str(FONTS / ("ComicNeue-Bold.ttf" if bold else "ComicNeue-Regular.ttf")),
                              round(size * 4))


class Drawing(Art):
    def __init__(self, name="arco_system_overview", title="ARCO system overview",
                 width=W, height=H, caption=CAPTION):
        super().__init__(name, title, width, height, caption)
        self.family = "Comic Neue"

    def measure(self, value, size, weight=400, family=None, italic=False):
        face = font(size, weight >= 600)
        _, top, _, bottom = face.getbbox(value, anchor="ls")
        return face.getlength(value)/4, (bottom-top)/4, -top/4

    def word(self, x, y, value, size=11, color=INK, *, bold=False, align="middle"):
        self.label(x, y, value, size, bold=bold, color=color, align=align)

    def save_to(self, directory):
        # Cairo uses Fontconfig, while standalone SVG viewers can load the inlined fonts.
        fc = ctypes.CDLL(ctypes.util.find_library("fontconfig"))
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        styles = []
        for weight, style in ((400, "Regular"), (700, "Bold")):
            path = FONTS / f"ComicNeue-{style}.ttf"
            if not fc.FcConfigAppFontAddFile(None, bytes(path)):
                raise RuntimeError(f"Could not register {path}")
            data = base64.b64encode(path.read_bytes()).decode()
            styles.append(f"@font-face{{font-family:'Comic Neue';font-weight:{weight};"
                          f"src:url(data:font/ttf;base64,{data}) format('truetype');}}")
        w_canvas, h_canvas = self.width, self.height
        assert all(x >= 0 and y >= 0 and x+w <= w_canvas and y+h <= h_canvas
                   for _, x, y, w, h in self.text_bounds)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="6.75in" '
               f'height="{6.75*h_canvas/w_canvas:.6f}in" viewBox="0 0 {w_canvas} {h_canvas}">'
               f'<title>{escape(self.title)}</title><desc>{escape(self.caption)}</desc>\n'
               + "\n".join(self.parts) + "</svg>")
        path = directory / self.filename
        path.write_text(svg.replace("<title>", "<style>" + "".join(styles) + "</style><title>", 1))
        cairosvg.svg2pdf(bytestring=svg.encode(), dpi=72, output_width=486,
                        output_height=486*h_canvas/w_canvas, write_to=str(path.with_suffix(".pdf")))
        cairosvg.svg2png(bytestring=svg.encode(), output_width=4050,
                                  output_height=round(4050*h_canvas/w_canvas), write_to=str(path.with_suffix(".png")))


def box(f, x, y, w, h, fill=PAPER, edge=INK, radius=6, width=1.25):
    f.rect(x, y, w, h, fill, edge, width, radius)


def arrow(f, pts, color=INK, *, dashed=False, width=1.35, head=5):
    f.path(pts, color, width, "3 2.4" if dashed else None)
    f.arrowhead(*pts[-2:], color, width, head)


def agent(f, x, y, name, fill=ICE, edge=BLUE):
    box(f, x-10, y-8, 20, 16, fill, edge, 5)
    f.word(x, y, name, 12, bold=True)


def robot(f, x, y):
    f.line(x, y-10, x, y-16, INK, 1.4)
    f.circle(x, y-17, 2.6, GOLD, INK, 1)
    for dx in (-17, 14):
        box(f, x+dx, y-3, 4, 8, SKY, INK, 1.5)
    box(f, x-14.5, y-10.5, 29, 22, SKY, INK, 7)
    box(f, x-11, y-6.5, 22, 13, INK, INK, 4)
    for dx in (-4.7, 4.7):
        f.circle(x+dx, y-.5, 2.1, "#abf3f1", "none", 0)


def clock(f, x, y, r=6):
    f.circle(x, y, r, CREAM, RUST, 1.2)
    f.path(((x, y-r*.62), (x, y), (x+r*.49, y+r*.27)), RUST, 1.25)


def check(f, x, y, color):
    f.path(((x-3.4, y), (x-.8, y+2.4), (x+3.8, y-3.2)), color, 1.8)


def page(f, x, y, val, fill, edge, *, dashed=False, w=16):
    f.rect(x, y, w, 13, fill, edge, .9, 2, "2 1.6" if dashed else None)
    if val:
        f.word(x+w/2, y+6.3, val, 9.8, bold=True)


def policy_frame(f, x, index, title, subtitle, edge, fill, *, width=182):
    box(f, x, 79, width, 210, PAPER, INK, 10, 1.7)
    box(f, x+9, 88, 24, 18, edge, edge, 4)
    f.word(x+21, 97, index, 12.6, PAPER, bold=True)
    f.word(x+39, 97, title, 14.6, bold=True, align="start")
    f.word(x+11, 116, subtitle, 11.5, edge, align="start", bold=True)
    f.line(x+10, 125, x+width-10, 125, edge, .75)
    box(f, x+8, 259, width-16, 22, fill, edge, 4, 1)


def workflows(f):
    box(f, 7, 43, 144, 246, PAPER, INK, 10, 1.7)
    f.word(79, 58, "Agent workflows", 16.3, bold=True)
    robot(f, 30, 92)
    f.word(54, 85, "Parent P", 13, bold=True, align="start")
    f.word(54, 100, "spawns c1, c2", 11.7, align="start")
    box(f, 17, 115, 124, 64, ICE, BLUE, 5, .9)
    for yy in (133, 161):
        arrow(f, ((43, 147), (61, yy)), BLUE, head=4.5)
        arrow(f, ((85, yy), (104, 147)), BLUE, dashed=True, head=4.5)
    for x, y, name in ((31, 147, "P"), (73, 133, "c1"), (73, 161, "c2"), (118, 147, "P")):
        agent(f, x, y, name, PAPER)
    f.word(116, 167, "JOIN", 9.2, BLUE, bold=True)
    agent(f, 29, 198, "R", LEAF, GREEN)
    f.word(46, 198, "New root arrives", 11.7, align="start")
    clock(f, 29, 221)
    f.word(46, 221, "c1 waits for a tool", 10.8, align="start")
    f.circle(29, 244, 5.7, LEAF, GREEN, 1.1)
    check(f, 29, 244, GREEN)
    f.word(46, 244, "c2 ready to run", 11.2, align="start")
    f.line(17, 257, 141, 257, "#c9d6df", .8)
    f.word(79, 271, "Observed dependencies", 11.2, BLUE, bold=True)


def admission(f, x):
    policy_frame(f, x, "D1", "First KV prefill", "Two-tier space pressure", BLUE, ICE)
    for y, label, vals in ((138, "HBM", ("used",)*3+("free",)*3),
                           (161, "Host", ("live",)*3+("copy",)*2+("free",))):
        f.word(x+12, y+6, label, 11.7, bold=True, align="start")
        for i, item in enumerate(vals):
            page(f, x+49+19*i, y, "", {"free": PAPER, "used": SKY, "live": GOLD, "copy": MINT}[item],
                 {"free": "#8d9daa", "used": BLUE, "live": RUST, "copy": GREEN}[item], w=16)
    for dx, label, fill, edge in ((12, "live only", GOLD, RUST), (83, "safe copy", MINT, GREEN)):
        box(f, x+dx, 182, 8, 8, fill, edge, 1, .8)
        f.word(x+dx+12, 186, label, 10.2, align="start")
    f.word(x+91, 205, "6 eligible  -  3 reserved  =  3", 12.2, bold=True)
    f.word(x+91, 220, "2 units per first request", 10.4, GRAY)
    agent(f, x+21, 237, "c2")
    f.word(x+36, 237, "Permit", 11.7, GREEN, bold=True, align="start")
    agent(f, x+102, 237, "R", LEAF, GREEN)
    f.word(x+118, 237, "Hold", 11.7, RUST, bold=True, align="start")
    f.word(x+91, 270, "Recheck at native prefill", 12, BLUE, bold=True)


def parent_recovery(f, x):
    policy_frame(f, x, "D2", "Parent KV recovery", "Backup on parent events", GREEN, LEAF)
    f.word(x+12, 138, "SPAWN / WAIT_JOIN", 11.4, bold=True, align="start")
    for y, tier in ((151, "HBM"), (180, "Host")):
        f.word(x+12, y+6, tier, 11, align="start", bold=True)
        for i in range(3):
            page(f, x+56+i*22, y, str(i+1), ICE if i<2 else LEAF,
                 BLUE if i<2 else GREEN, dashed=tier=="Host" and i==2, w=18)
    arrow(f, ((x+109, 164), (x+109, 179)), GREEN, head=4.4)
    f.word(x+119, 171, "D2H", 10.5, GREEN, bold=True, align="start")
    f.word(x+12, 205, "Release on HBM deficit after ACK", 10.8, align="start")
    f.line(x+12, 215, x+170, 215, "#b4d4c3", .7)
    f.word(x+12, 227, "Last child c2 emits final EOS", 11.2, align="start", bold=True)
    f.word(x+13, 246, "Host", 11, align="start")
    arrow(f, ((x+42, 246), (x+93, 246)), GREEN, head=4)
    f.word(x+67, 240, "H2D", 8.8, GREEN, bold=True)
    f.word(x+100, 246, "Parent KV", 11, align="start")
    f.word(x+91, 270, "Run P after actual JOIN", 12, GREEN, bold=True)


def tool_offload(f, x):
    policy_frame(f, x, "D3", "Tool-aware offload", "Ready c2 needs device pages", RUST, CREAM)
    clock(f, x+18, 141)
    f.word(x+31, 141, "c1's remaining tool wait", 11.3, align="start", bold=True)
    # Bars are schematic service windows, aligned at the decision instant.
    for y, width, label, fill, edge in ((159, 83, "Wait", GOLD, RUST),
                                        (175, 47, "Move", SKY, BLUE)):
        f.word(x+12, y, label, 10.9, align="start")
        box(f, x+52, y-4.1, width, 8.2, fill, edge, 2, .9)
    f.line(x+99, 152, x+99, 183, INK, .7, "2 2")
    f.word(x+12, 194, "Likely to cover the round trip?", 10.7, bold=True, align="start")
    f.line(x+12, 205, x+170, 205, "#ddcbb7", .7)
    for yy, word, color in ((218, "Predictive release", RUST), (241, "2 s working-set check", PURPLE)):
        f.circle(x+15, yy, 2.4, color, color, .3)
        f.word(x+22, yy, word, 10.8, color, align="start", bold=True)
    f.path(((x+133, 218), (x+143, 218), (x+143, 241), (x+133, 241)), GRAY, .9)
    arrow(f, ((x+143, 230), (x+158, 230), (x+158, 256)), GRAY, head=4.4)
    f.word(x+91, 270, "Recheck donor & HBM deficit", 11.6, RUST, bold=True)


def chip(f, x, y):
    for shift in (-9, -3, 3, 9):
        f.line(x+shift, y-14, x+shift, y+14, BLUE, 1.6)
        f.line(x-15, y+shift, x+15, y+shift, BLUE, 1.6)
    box(f, x-12, y-11.5, 24, 23, SKY, INK, 3)
    box(f, x-7, y-6.5, 14, 13, ICE, BLUE, 2, .9)


def engine(f):
    box(f, 7, 316, 746, 79, "#f4f8fc", INK, 9, 1.7)
    f.word(20, 332, "SGLang serving engine", 15.8, bold=True, align="start")
    f.word(20, 355, "Free pages, transfer ACKs", 11.5, BLUE, align="start")
    f.word(20, 371, "and final EOS", 11.5, BLUE, align="start")
    for x in (167, 371, 566):
        f.line(x, 342, x, 385, "#adbdc8", .8)
    for y, row in ((350, (1, 1, 1, 0)), (361, (1, 1, 0, 0)), (372, (1, 1, 1, 1))):
        for i, on in enumerate(row):
            box(f, 181+i*7.7, y, 6.5, 6.5, SKY if on else PAPER, BLUE, 1.1, .65)
    f.word(223, 354, "Native scheduler", 13.3, bold=True, align="start")
    f.word(223, 372, "Queue, KV and slot checks", 10.7, align="start")
    for p,q in (((401,350),(390,362)),((401,350),(413,362)),((390,362),(384,374)),
                ((390,362),(398,374))):
        f.line(*p,*q, GREEN, 1.45)
    for px,py in ((401,350),(390,362),(413,362),(384,374),(398,374)):
        f.circle(px, py, 3.2, MINT, GREEN, .9)
    f.word(426, 354, "Radix prefix cache", 13.3, bold=True, align="start")
    f.word(426, 372, "Ownership, locks, transfers", 10.7, align="start")
    chip(f, 592, 360)
    f.word(592, 382, "HBM", 9.8, bold=True)
    arrow(f, ((615, 355), (647, 355)), BLUE, head=4.4)
    arrow(f, ((647, 366), (615, 366)), GREEN, head=4.4)
    box(f, 658, 351, 66, 18, MINT, GREEN, 2, 1.1)
    for i in range(4):
        box(f, 663+15*i, 355, 10, 8, PAPER, GREEN, 1.2, .8)
    for i in range(10):
        f.line(661+6.1*i, 369, 661+6.1*i, 372, RUST, 1.5)
    f.word(691, 382, "Host DRAM", 9.8, bold=True)


def make_figure():
    f = Drawing()
    f.word(10, 19, "ARCO", 25, bold=True, align="start")
    f.word(82, 20, "Workflow-aware KV management", 18.4, bold=True, align="start")
    arrow(f, ((519, 18), (538, 18)), INK)
    f.word(543, 18, "control action", 11.4, align="start")
    arrow(f, ((634, 18), (653, 18)), BLUE, dashed=True)
    f.word(658, 18, "observation", 11.4, align="start")
    workflows(f)
    policy_starts = (174, 372, 570)
    for x in policy_starts:
        arrow(f, ((x+91, 62), (x+91, 77)), BLUE, dashed=True)
    box(f, 174, 43, 578, 20, ICE, BLUE, 5, 1.2)
    f.word(463, 53, "ARCO runtime   /   lifecycle events, ready requests and cache state",
           12.5, BLUE, bold=True)
    arrow(f, ((151, 52), (172, 52)), BLUE, dashed=True)
    arrow(f, ((158, 316), (158, 58), (172, 58)), BLUE, dashed=True)
    admission(f, policy_starts[0])
    parent_recovery(f, policy_starts[1])
    tool_offload(f, policy_starts[2])
    engine(f)
    arrow(f, ((53, 290), (53, 314)), INK, width=1.7, head=5.8)
    f.word(61, 302, "Submit requests", 11, bold=True, align="start")
    for x, action in zip(policy_starts, ("Permit / hold", "Prepare / recover", "Back up / release")):
        cx = x+91
        arrow(f, ((cx, 290), (cx, 314)), INK, width=1.7, head=5.8)
        f.word(cx+8, 302, action, 11.0, bold=True, align="start")
    return f


if __name__ == "__main__":
    folder = OUTPUT / "review"
    folder.mkdir(parents=True, exist_ok=True)
    f = make_figure()
    f.save_to(folder)
    ppt = PowerPointExporter("en", width=W, height=H)
    ppt.add_figure(f, folder)
    ppt.presentation.slides[0].notes_slide.notes_text_frame.text = CAPTION
    ppt.presentation.core_properties.title = f.title
    print(ppt.save(folder / (Path(f.filename).stem+"_editable.pptx"), expected_slides=1))
