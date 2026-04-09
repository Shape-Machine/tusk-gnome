import math

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')

from gi.repository import Gtk, Pango, PangoCairo  # noqa: E402

NODE_W   = 192
NODE_H   = 78
H_GAP    = 20   # horizontal gap between sibling subtrees
V_GAP    = 52   # vertical gap between levels
MARGIN   = 28
CORNER_R = 9


class ExplainGraph(Gtk.DrawingArea):
    """Cairo-rendered top-down EXPLAIN plan node graph."""

    def __init__(self):
        super().__init__()
        self._nodes = []   # (cx, cy, node_dict, cost_ratio)
        self._edges = []   # (x1, y1, x2, y2)
        self.set_draw_func(self._on_draw)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_plan(self, plan_json):
        self._nodes = []
        self._edges = []
        if not plan_json or not isinstance(plan_json, list):
            self.queue_draw()
            return
        top = plan_json[0].get('Plan', {})
        self._build_layout(top)
        self.queue_draw()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _subtree_width(self, node):
        kids = node.get('Plans', [])
        if not kids:
            return NODE_W
        kids_w = sum(self._subtree_width(k) for k in kids) + H_GAP * (len(kids) - 1)
        return max(NODE_W, kids_w)

    def _tree_depth(self, node):
        kids = node.get('Plans', [])
        return 1 + (max(self._tree_depth(k) for k in kids) if kids else 0)

    def _build_layout(self, root):
        max_cost = [1e-9]

        def _scan(n):
            max_cost[0] = max(max_cost[0], n.get('Total Cost', 0.0))
            for c in n.get('Plans', []):
                _scan(c)
        _scan(root)

        self._nodes = []
        self._edges = []

        def _place(node, left, level, parent_cx=None, parent_bottom=None):
            sw = self._subtree_width(node)
            cx = left + sw / 2
            cy = MARGIN + level * (NODE_H + V_GAP)
            ratio = node.get('Total Cost', 0.0) / max_cost[0]
            self._nodes.append((cx, cy, node, ratio))
            if parent_cx is not None:
                self._edges.append((
                    parent_cx, parent_bottom,
                    cx,        cy,
                ))
            kids = node.get('Plans', [])
            kx = left
            for k in kids:
                _place(k, kx, level + 1, cx, cy + NODE_H)
                kx += self._subtree_width(k) + H_GAP

        _place(root, MARGIN, 0)

        depth     = self._tree_depth(root)
        canvas_w  = self._subtree_width(root) + MARGIN * 2
        canvas_h  = MARGIN + depth * (NODE_H + V_GAP) + MARGIN
        self.set_content_width(int(canvas_w))
        self.set_content_height(int(canvas_h))

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _cost_color(self, ratio):
        """Map 0→1 cost ratio to an RGB colour: blue → teal → yellow → red."""
        if ratio < 0.33:
            t = ratio / 0.33
            return (0.11 + t * 0.10, 0.44 + t * 0.22, 0.85 - t * 0.35)
        if ratio < 0.66:
            t = (ratio - 0.33) / 0.33
            return (0.21 + t * 0.68, 0.66 - t * 0.25, 0.50 - t * 0.46)
        t = (ratio - 0.66) / 0.34
        return (0.89 + t * (0.75 - 0.89), 0.41 - t * 0.30, 0.04)

    def _rounded_rect(self, cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r,     r,  -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r,   0,            math.pi / 2)
        cr.arc(x + r,     y + h - r, r,   math.pi / 2,  math.pi)
        cr.arc(x + r,     y + r,     r,   math.pi,      3 * math.pi / 2)
        cr.close_path()

    def _on_draw(self, _area, cr, _width, _height):
        # Background — honour light/dark theme
        fg = self.get_style_context().get_color()
        dark = (fg.red + fg.green + fg.blue) / 3 > 0.5
        if dark:
            cr.set_source_rgb(0.13, 0.13, 0.13)
        else:
            cr.set_source_rgb(0.94, 0.94, 0.96)
        cr.paint()

        # Edges (bezier curves)
        cr.set_line_width(2)
        cr.set_source_rgba(0.55, 0.55, 0.55, 0.75)
        for x1, y1, x2, y2 in self._edges:
            mid_y = (y1 + y2) / 2
            cr.move_to(x1, y1)
            cr.curve_to(x1, mid_y, x2, mid_y, x2, y2)
            cr.stroke()

        # Nodes
        for cx, cy, node, ratio in self._nodes:
            self._draw_node(cr, cx, cy, node, ratio)

    def _draw_node(self, cr, cx, cy, node, ratio):
        x = cx - NODE_W / 2
        y = cy
        r, g, b = self._cost_color(ratio)

        # Filled rounded rectangle
        self._rounded_rect(cr, x, y, NODE_W, NODE_H, CORNER_R)
        cr.set_source_rgb(r, g, b)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.set_line_width(1)
        cr.stroke()

        # Node-type label (bold)
        node_type = node.get('Node Type', '?')
        relation  = node.get('Relation Name', '')
        title     = f'{node_type}\n{relation}' if relation else node_type

        lo = PangoCairo.create_layout(cr)
        lo.set_font_description(Pango.FontDescription.from_string('Sans Bold 9'))
        lo.set_width(int((NODE_W - 16) * Pango.SCALE))
        lo.set_ellipsize(Pango.EllipsizeMode.END)
        lo.set_alignment(Pango.Alignment.CENTER)
        lo.set_text(title, -1)

        cr.set_source_rgb(1, 1, 1)
        cr.move_to(x + 8, y + 7)
        PangoCairo.show_layout(cr, lo)

        # Stats line (small)
        cost        = node.get('Total Cost', 0.0)
        plan_rows   = node.get('Plan Rows', '?')
        actual_rows = node.get('Actual Rows')
        actual_time = node.get('Actual Total Time')

        parts = [f'cost {cost:.1f}', f'~{plan_rows}r']
        if actual_rows is not None:
            parts.append(f'actual {actual_rows}r')
        if actual_time is not None:
            parts.append(f'{actual_time:.1f}ms')

        lo2 = PangoCairo.create_layout(cr)
        lo2.set_font_description(Pango.FontDescription.from_string('Sans 7'))
        lo2.set_width(int((NODE_W - 16) * Pango.SCALE))
        lo2.set_ellipsize(Pango.EllipsizeMode.END)
        lo2.set_alignment(Pango.Alignment.CENTER)
        lo2.set_text(' · '.join(parts), -1)

        cr.set_source_rgba(1, 1, 1, 0.85)
        cr.move_to(x + 8, y + NODE_H - 22)
        PangoCairo.show_layout(cr, lo2)

    # ── Export ────────────────────────────────────────────────────────────────

    def render_to_png_bytes(self):
        import io
        import cairo
        w = self.get_content_width()
        h = self.get_content_height()
        if not w or not h:
            return None
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surface)
        self._on_draw(None, cr, w, h)
        buf = io.BytesIO()
        surface.write_to_png(buf)
        return buf.getvalue()

    def render_to_svg_bytes(self):
        import io
        import cairo
        w = self.get_content_width()
        h = self.get_content_height()
        if not w or not h:
            return None
        buf = io.BytesIO()
        surface = cairo.SVGSurface(buf, w, h)
        cr = cairo.Context(surface)
        self._on_draw(None, cr, w, h)
        surface.finish()
        return buf.getvalue()
