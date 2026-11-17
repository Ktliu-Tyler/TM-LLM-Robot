"""Rebuild the current implementation diagram; optional resvg_py creates PNG."""
from html import escape
from pathlib import Path

out = Path(__file__).parent
s = ['<svg xmlns="http://www.w3.org/2000/svg" width="3200" height="2200" viewBox="0 0 1600 1100">',
     '<rect width="1600" height="1100" fill="#102333"/>',
     '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10" fill="#8acbc9"/></marker></defs>']


def text(x, y, value, size=22, color="#e9f5f5"):
    s.append(f'<text x="{x}" y="{y}" fill="{color}" font-family="Arial,sans-serif" font-size="{size}">{escape(value)}</text>')


def box(x, y, w, title, lines, color="#194b59"):
    s.append(f'<rect x="{x}" y="{y}" width="{w}" height="140" rx="14" fill="{color}" stroke="#527987"/>')
    text(x+20, y+36, title, 27)
    for i, line in enumerate(lines):
        text(x+20, y+70+i*27, line, 20, "#bdd9df")


def edge(points, both=False):
    s.append('<polyline points="'+points+'" fill="none" stroke="#8acbc9" stroke-width="3" marker-end="url(#a)"'+(' marker-start="url(#a)"' if both else '')+'/>')


text(50, 65, "Jarvis / Workbench — Current Implementation", 42)
text(50, 105, "Voice conversation + inventory tools + movement dispatch | 2026-11-17", 24, "#a6c4cc")
box(50, 160, 430, "Control Room :8765", ["admin / embedded chat :8767", "Node switching, results, API trace", "Old and new agents are exclusive"])
box(1110, 160, 440, "Legacy workbench_agent", ["Kept available in the control room", "Same Inventory / arm interfaces", "Runs only when Jarvis is stopped"], "#344553")
box(50, 390, 430, "Voice Worker", ["Jarvis KWS + Qwen3-ASR-0.6B", "Wake / speech endpoints / transcript", "Task end or idle -> standby"])
box(570, 390, 450, "workbench_chat / Jarvis", ["Session memory + native tool loop", "inventory / workbench_action", "Validation + per-turn replay guard"])
box(1110, 390, 440, "Ollama / Qwen3.5-4B", ["Natural multi-turn conversation", "Tool calls and grounded replies", "Streaming content / optional thinking"])
box(50, 640, 430, "Windows TTS + paplay", ["Progress notice / final response", "Jarvis interrupts final audio only", "Does NOT cancel dispatched motion"])
box(570, 640, 450, "workbench_state / Inventory", ["Queries / rules / task planning", "Source, destination, storage checks", "simulate_motion selects execution"])
box(1110, 640, 440, "SQLite / Workbench", ["Objects, owners, locations, homes", "move / set_home update records", "Simulation commits planned moves"])
box(570, 880, 450, "Existing Arm Command Topic", ["/project_a/arm/command", "place_object / return_object", "task_id + source / target locations"], "#694c2f")
box(1110, 880, 440, "Completion Boundary", ["Normal mode: dispatched, not done", "No physical completion feedback yet", "Simulation: no arm messages"], "#694c2f")
edge('480,210 1110,210'); text(650,195,"Mutually exclusive switch",21)
edge('300,300 300,345 795,345 795,390',True);text(550,330,"Manage / agent result / chat trace",20)
edge('480,460 570,460');text(490,445,"Text",18)
edge('1020,460 1110,460',True);text(1028,445,"HTTP",18)
edge('795,530 795,640',True);text(815,590,"Inventory.srv",20)
edge('1020,710 1110,710',True)
edge('570,495 520,495 520,710 480,710');text(65,615,"Final text -> speech -> listen again",20)
edge('1020,510 1060,510 1060,845 795,845 795,880');text(1090,820,"Normal mode only",20)
text(50,1045,"Actions: pick / place / restore / tidy / set_home / move (registration only)",25)
text(50,1080,"Voice interruption and task-end close conversation stages; they are not robot emergency-stop commands.",21,"#efc68a")
s.append('</svg>')
svg = ''.join(s)
(out/'JARVIS_ARCHITECTURE.svg').write_text(svg, encoding='utf-8')
try:
    import resvg_py
except ImportError:
    print('SVG ready; install resvg-py to render PNG')
else:
    (out/'JARVIS_ARCHITECTURE.png').write_bytes(resvg_py.svg_to_bytes(svg_string=svg))
