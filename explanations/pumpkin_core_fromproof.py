"""
Extract the user-level CPMpy constraints that participate in a Pumpkin DRCP proof.
"""
import gzip

from cpmpy.expressions.core import Expression, Operator
from cpmpy.expressions.utils import is_any_list
from cpmpy.solvers.pumpkin import CPM_pumpkin
from cpmpy.transformations.get_variables import get_variables


class PumpkinProofCore(CPM_pumpkin):
    """
    Pumpkin solver that maps proof constraint tags back to user-level CPMpy constraints.

    After solving with proof logging, :meth:`get_core` trims the DRCP proof
    backwards from the conclusion and returns the original constraints whose tags
    remain.
    """

    def __init__(self, cpm_model=None, proof=None, **kwargs):
        self.tag_cons: list[tuple[int, Expression]] = []
        super().__init__(cpm_model=cpm_model, proof=proof, **kwargs)

    def add(self, cpm_expr):
        """
        Post constraints, recording each user-level CPMpy constraint with the Pumpkin tag
        shared by the solver constraints it was transformed into.
        """
        if self.pum_solver.is_inconsistent():
            return self

        get_variables(cpm_expr, collect=self.user_vars)

        user_cons = cpm_expr
        if not is_any_list(cpm_expr):
            user_cons = [cpm_expr]

        for user_con in user_cons:
            tag = self.pum_solver.new_constraint_tag()
            self.tag_cons.append((int(tag), user_con))
            try:
                for con in self.transform(user_con):
                    # post the constraint (or implication) to the Pumpkin solver with this tag
                    if isinstance(con, Operator) and con.name == "->":
                        bv, subexpr = con.args
                        for pum_cons in self._get_constraint(subexpr, tag=tag):
                            self.pum_solver.add_implication(pum_cons, self.solver_var(bv))
                    else:
                        for pum_cons in self._get_constraint(con, tag=tag):
                            self.pum_solver.add_constraint(pum_cons)
            except RuntimeError as e:
                if not self.pum_solver.is_inconsistent():
                    raise e
                # else: it is inconsistent, but we still need to record the tags
            if self.pum_solver.is_inconsistent():
                break
        return self
    __add__ = add

    def get_core(self, proof=None):
        """
        Return the user-level CPMpy constraints that participated in the DRCP proof.

        Walks inference and nogood steps from back to front, keeping a step only if
        its id is used by a later kept step (the last step is always kept). Unused
        steps are dropped. A posted constraint is in the core if its tag remains.
        Learned-nogood ids that do not correspond to a posted constraint are ignored.

        Similar to the assumption-based ``get_core`` of other CPMpy solvers, but
        does not require assumption variables.

        Arguments:
            proof (str, optional): path to the DRCP proof file (default: the path given at construction)

        Returns:
            list of CPMpy constraints that were used in the proof, in posting order
        """
        # extract steps from proof file
        steps = self.get_steps(proof)

        # trim the proof steps
        steps = self.trim_steps(steps)
        if not steps:
            raise ValueError(f"No inference steps found in proof, {proof or self._proof}")

        # collected the tags used by the (inference) steps
        used_tags = set()
        for _, kind, deps in steps:
            # constraint tags only appear as deps of inference steps
            if kind == "i":
                used_tags.update(deps)
        
        # map the tags back to the original constraints and return those
        return [c for t, c in self.tag_cons if t in used_tags]

    def get_steps(self, proof=None) -> list[tuple[int, str, list[int]]]:
        """Parse inference and nogood steps from the DRCP proof file."""
        if proof is None:
            proof = self._proof
        assert proof is not None, "requires proof logging, e.g. PumpkinProofCore(model, proof='proof.drcp')"

        if str(proof).endswith(".gz"):
            open_file = gzip.open(proof, "rt")
        else:
            open_file = open(proof, "r")

        steps: list[tuple[int, str, list[int]]] = []
        with open_file as f:
            for line in f:
                if line.startswith("i "):
                    # i <id> ... [c:<tag>] [l:<algo>]
                    parts = line.split()
                    deps = [int(p[2:]) for p in parts[2:] if p.startswith("c:")]
                    steps.append((int(parts[1]), "i", deps))
                elif line.startswith("n "):
                    # n <id> <lits> [0 <hint step ids>]
                    parts = line.split()
                    rest = parts[2:]
                    deps = []
                    if "0" in rest:
                        deps = [int(p) for p in rest[rest.index("0") + 1:]]
                    steps.append((int(parts[1]), "n", deps))
        return steps

    def trim_steps(self, steps: list[tuple[int, str, list[int]]]) -> list[tuple[int, str, list[int]]]:
        """Keep a step only if used by a later kept step (the last step is always kept)."""
        if not steps:
            return []
        used = {steps[-1][0]}
        for step_id, _, deps in reversed(steps):
            if step_id in used:
                used.update(deps)
        return [s for s in steps if s[0] in used]


    def visualize_proof(self, proof=None, trimmed=True, ax=None):
        """Draw the DRCP proof as a two-column dependency DAG."""
        import matplotlib.pyplot as plt
        import networkx as nx
        from matplotlib.patches import Patch

        steps = self.get_steps(proof)
        if trimmed:
            steps = self.trim_steps(steps)
        if not steps:
            raise ValueError(f"No inference steps found in proof, {proof or self._proof}")

        tag_to_cons = dict(self.tag_cons)
        step_ids = {sid for sid, _, _ in steps}

        G = nx.DiGraph()
        for sid, kind, _ in steps:
            G.add_node(sid, kind=kind, label=f"{kind}{sid}")
        for sid, _, deps in steps:
            for d in deps:
                if d in tag_to_cons and d not in step_ids:
                    G.add_node(d, kind="constraint", label=str(tag_to_cons[d]))
                if d in G:
                    G.add_edge(d, sid)

        # two columns: constraints on first-use row, steps in a slight rightward staircase
        left = [t for t, _ in self.tag_cons if t in G]
        right = [sid for sid, _, _ in steps]
        pos = {n: (0.22 + 0.025 * i, -i) for i, n in enumerate(right)}
        row = {n: i for i, n in enumerate(right)}
        by_row: dict[int, list] = {}
        for t in left:
            first = min((row[s] for s in G.successors(t) if s in row), default=0)
            by_row.setdefault(first, []).append(t)
        for r, nodes in by_row.items():
            for k, t in enumerate(nodes):
                pos[t] = (0.0, -r - 0.35 * (k - (len(nodes) - 1) / 2))

        if ax is None:
            n_rows = max(len(left), len(right), 1)
            _, ax = plt.subplots(figsize=(7, max(4, n_rows * 0.45)))

        colors = {"constraint": "tab:blue", "i": "tab:orange", "n": "tab:green"}
        right_set = set(right)
        cross = [(u, v) for u, v in G.edges() if not (u in right_set and v in right_set)]
        vert = [(u, v) for u, v in G.edges() if u in right_set and v in right_set]
        if cross:
            nx.draw_networkx_edges(
                G, pos, ax=ax, edgelist=cross,
                arrows=True, arrowstyle="-|>", arrowsize=12,
                edge_color="0.55", width=1.2, node_size=280,
                connectionstyle="arc3,rad=0",
            )
        if vert:
            nx.draw_networkx_edges(
                G, pos, ax=ax, edgelist=vert,
                arrows=True, arrowstyle="-|>", arrowsize=12,
                edge_color="0.45", width=1.1, node_size=280,
                connectionstyle="arc3,rad=-0.3",
            )
        nx.draw_networkx_nodes(
            G, pos, ax=ax, nodelist=list(G),
            node_color=[colors[G.nodes[n]["kind"]] for n in G], node_size=280,
        )
        if right:
            nx.draw_networkx_labels(
                G, pos, ax=ax, labels={n: G.nodes[n]["label"] for n in right},
                font_size=5, font_color="white", font_weight="bold",
            )
        left_texts = {}
        if left:
            # constraint names left of the balls
            label_pos = {n: (pos[n][0] - 0.05, pos[n][1]) for n in left}
            left_texts = nx.draw_networkx_labels(
                G, label_pos, ax=ax, labels={n: G.nodes[n]["label"] for n in left},
                font_size=16,
                horizontalalignment="right", verticalalignment="center",
            )
        ax.legend(
            handles=[
                Patch(facecolor=colors["constraint"], label="constraint"),
                Patch(facecolor=colors["i"], label="inference"),
                Patch(facecolor=colors["n"], label="nogood"),
            ],
            loc="upper right", frameon=False, fontsize=16,
        )
        ax.set_axis_off()
        ax.set_aspect("auto")
        ys = [y for _, y in pos.values()]
        xs = [x for x, _ in pos.values()]
        # fit xlim to rendered constraint labels
        if left_texts:
            ax.figure.canvas.draw()
            renderer = ax.figure.canvas.get_renderer()
            bboxes = [t.get_window_extent(renderer).transformed(ax.transData.inverted())
                      for t in left_texts.values()]
            xmin = min(b.x0 for b in bboxes) - 0.02
        else:
            xmin = min(xs) - 0.08
        ax.set_xlim(xmin, max(xs) + 0.08)
        ax.set_ylim(min(ys) - 0.3, max(ys) + 0.3)
        ax.figure.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
        return G, ax


if __name__ == "__main__":
    import cpmpy as cp
    import matplotlib.pyplot as plt

    x, y, z = cp.intvar(1, 2, shape=3, name=list("xyz"))
    a, b = cp.intvar(0, 10, shape=2, name=list("ab"))
    #model = cp.Model(cp.AllDifferent([x, y]), x == 1, y == 1, a + b <= 15, x >= y)
    model = cp.Model(cp.AllDifferent([x, y]), x == z, x <= 1, a + b <= 15, y == 1, x >= y)

    solver = PumpkinProofCore(model, proof="alldiff.drcp")
    assert solver.solve() is False
    print("Constraints used in trimmed proof:")
    for cons in solver.get_core():
        print(" ", cons)

    solver.visualize_proof(trimmed=False)
    plt.show()
