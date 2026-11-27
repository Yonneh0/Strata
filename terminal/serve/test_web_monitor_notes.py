"""#5457: the web Monitor says why a reading it cannot take is empty (the server's gpu_note, as the card's line "not
available" and its tooltip) also when the GPU has a name - a Mac always has one - and About shows the reason beside
the name. The temperature and the PCIe traffic are graphs of the power and the load cards now, so their "not available"
is the line under the graph rather than a card of its own. serve/web/app.js is run in node with stand-ins for the page's
helpers (no browser)."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"
NODE = shutil.which("node")
MAC_NOTE = ("no GPU temperature or power reading without root (powermetrics), and an Apple Silicon GPU sits on the "
            "die, so there is no PCIe link to show; the engine's own figures are in its log")


def run_node(source: str) -> object:
    done = subprocess.run([NODE, "-e", source], capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    return json.loads(done.stdout)


def function_source(text: str, name: str) -> str:
    start = text.index(f"function {name}(")
    return text[start:text.index("\n}\n", start) + 3]


def gpu_cards_block(text: str) -> str:
    """renderMonitor's lines from the GPU load card to its PCIe graph's own line."""
    start = text.index("  const per = (f) =>")
    return text[start:text.index('  setMetric("cpu"', start)]


STUBS = """
const els = {};
const $ = (id) => (els[id] ||= {id, textContent: "", innerHTML: "", title: ""});
const esc = (s) => String(s);
const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toFixed(d));
const gb = (b, d = 1) => (b == null ? "–" : fmt(b / 1073741824, d));
const spark = () => {};
"""


@unittest.skipUnless(NODE, "node is needed to run the page's own code")
class GpuNote(unittest.TestCase):
    def cards(self, hw, st, eng=None, h=None):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        script = (STUBS + function_source(app, "setMetric") + function_source(app, "stat") +
                  function_source(app, "statLine") + function_source(app, "setSide") +
                  function_source(app, "ramSticks") + function_source(app, "ramSticksTip") +
                  f"function run(hw, st, eng, h) {{\n{gpu_cards_block(app)}\n}}\n"
                  f"run({json.dumps(hw)}, {json.dumps(st)}, {json.dumps(eng or {})}, {json.dumps(h or {})});\n"
                  "console.log(JSON.stringify({gpu: [$('ms-gpu').textContent, $('ms-gpu').title],"
                  " clock: [$('mv-gpu-clock').innerHTML, $('ms-gpu-clock').textContent],"
                  " power: [$('mv-power').innerHTML, $('ms-power').textContent, $('ms-power').title],"
                  " temp: [$('mv-power').innerHTML, $('sn-sp-power-temp').textContent, $('sn-sp-power-temp').title],"
                  " pcie: [$('sn-sp-gpu-pcie').textContent, $('sn-sp-gpu-pcie').title],"
                  " ram: [$('mv-ram').innerHTML, $('ms-ram').textContent, $('ms-ram').title, $('sn-sp-ram').textContent]}));")
        return run_node(script)

    MAC_HW = {"gpu_util": 95, "gpu_mem_used": 88_000_000_000, "gpu_mem_total": 131_000_000_000, "gpu_temp": None,
              "gpu_power": None, "gpus": [{"index": 0}]}
    MAC_ST = {"gpu_name": "Apple M5 Max", "gpu_note": MAC_NOTE}

    def test_the_memory_card_prints_the_sticks_the_firmware_reports(self):
        hw = {"ram_used": 33_523_404_800, "ram_total": 68_553_834_496, "ram_sticks": 2, "ram_stick_gib": 32,
              "ram_type": "DDR4", "ram_mt_s": 2667, "ram_rated_mt_s": 3200, "ram_channels": 2}
        cards = self.cards(hw, {}, h={"ram_used": [hw["ram_used"], hw["ram_used"] + 500_000_000]})
        self.assertEqual(cards["ram"][0], "31.2<small>/ 64 GB</small>", "the totals the machine has, not a percentage")
        self.assertEqual(cards["ram"][1], "2 × 32 GB DDR4-2667 · dual channel")
        self.assertIn("rated 3200 MT/s, so this machine is not running its memory at the speed they are made for",
                     cards["ram"][2], "sticks running slower than they are rated is a setting, and the card says so")
        self.assertEqual(cards["ram"][3], "min 49 · max 50 · avg 49%", "the share of it in use, over the minute of samples")

    def test_the_memory_card_says_why_when_the_firmware_keeps_its_sticks_from_us(self):
        # Linux hands the firmware's stick details only to root, and a Windows whose WMI did not answer is in the same
        # position: the line says so, because a blank under a card is a thing the operator has to guess about
        hw = {"ram_used": 27_000_000_000, "ram_total": 32_891_662_336,
              "ram_note": "What the sticks themselves are is read from the machine's firmware, and this operating "
                          "system hands that out only to root."}
        cards = self.cards(hw, {}, h={"ram_used": [hw["ram_used"]]})
        self.assertIn("read from the machine's firmware", cards["ram"][1])
        self.assertEqual(cards["ram"][3], "min 82 · max 82 · avg 82%")

    def test_a_named_mac_gpu_explains_every_empty_reading_of_its_two_cards(self):
        cards = self.cards(self.MAC_HW, self.MAC_ST)
        self.assertEqual(cards["power"][1:], ["not available", MAC_NOTE])
        self.assertEqual(cards["temp"][1:], ["not available", MAC_NOTE], "the temperature graph of the power card")
        self.assertEqual(cards["pcie"], ["not available", MAC_NOTE], "the PCIe graph of the load card")
        self.assertEqual(cards["clock"], ["–", "not available"], "and the clock printed beside the load")
        self.assertEqual(cards["gpu"][0], "Apple M5 Max", "the load card keeps the GPU's name")
        self.assertEqual(cards["gpu"][1], "", "and no reason: it has a reading")

    def test_a_card_with_a_reading_or_no_note_says_nothing_more(self):
        nvidia = {"gpu_util": 50, "gpu_temp": 61, "gpu_power": 210, "gpu_power_limit": 450, "gpu_pcie_gen": 4,
                  "gpu_pcie_width": 16, "gpu_pcie_rx_mb": 12.5, "gpus": [{"index": 0}]}
        cards = self.cards(nvidia, {"gpu_name": "NVIDIA GeForce RTX 4090"})
        self.assertEqual(cards["power"], ["210<small>W</small>", "of 450 W limit", ""])
        self.assertEqual(cards["pcie"], ["", ""], "no samples to average: no numbers, and nothing invented")
        quiet = self.cards({"gpu_temp": None, "gpu_power": None, "gpus": [{"index": 0}]}, {"gpu_name": "Some GPU"})
        self.assertEqual(quiet["power"][1:], ["", ""], "no note: nothing invented")

    def test_the_temperature_and_the_link_print_the_minute_their_graph_is_drawn_from(self):
        hw = {"gpu_power": 210, "gpu_power_limit": 450, "gpu_temp": 61, "gpu_pcie_gen": 5, "gpu_pcie_gen_max": 5,
              "gpu_pcie_width": 16, "gpu_pcie_rx_mb": 1800, "gpu_pcie_tx_mb": 300, "gpus": [{"index": 0}]}
        cards = self.cards(hw, {"gpu_name": "RTX 5090"}, h={"gpu_temp": [48, 55, 61], "gpu_pcie_rx_mb": [1200, 1500, 1800]})
        self.assertEqual(cards["temp"][1], "61°C · min 48 · max 61 · avg 55°C")
        self.assertEqual(cards["pcie"][0], "Gen5 x16 · 1.8 GB/s")
        self.assertIn("The card sends 300.0 MB/s", cards["pcie"][1])
        self.assertIn("the same window the graph above is drawn from", cards["pcie"][1])

    def test_about_shows_the_reason_beside_a_named_gpu(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        script = (STUBS + "const rows = {}; const facts = (el, r) => { rows[el.id] = r; }; const projectionText = () => null;\n"
                  "globalThis.location = {origin: 'http://127.0.0.1:8080'};\n" + function_source(app, "renderAbout") +
                  f"renderAbout({{}}, {json.dumps(self.MAC_HW)}, {json.dumps(self.MAC_ST)});\n"
                  "console.log(JSON.stringify(rows['facts-hw'].find(r => r[0] === 'GPU')[1]));")
        self.assertEqual(run_node(script), f"Apple M5 Max, 122 GB · {MAC_NOTE}")


if __name__ == "__main__":
    unittest.main()
