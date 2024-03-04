
import pandas as pd
import numpy as np

import matplotlib.pyplot as plt


def visualize(sol, factory, highlight_cover=False):
    weeks = [f"Week {i + 1}" for i in range(factory.data.horizon // 7)]
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    nurses = factory.data.staff['name'].tolist()

    df = pd.DataFrame(sol,
                      columns=pd.MultiIndex.from_product((weeks, weekdays)),
                      index=factory.data.staff.name)

    mapping = factory.idx_to_name
    df = df.applymap(lambda v: mapping[v] if v is not None and v < len(mapping) else '')  # convert to shift names

    for shift_type in factory.shift_name_to_idx:
        if shift_type == "F":
            continue
        sums = (df == shift_type).sum()  # cover for each shift type
        req = factory.data.cover["Requirement"][factory.data.cover["ShiftID"] == shift_type]
        req.index = sums.index
        df.loc[f'Cover {shift_type}'] = sums.astype(str) + "/" + req.astype(str)
    df["Total shifts"] = (df != "F").sum(axis=1)  # shifts done by nurse

    subset = (df.index.tolist()[:-len(factory.data.shifts)], df.columns[:-1])
    styler = df.style\
        .applymap(lambda v: 'border: 1px solid black', subset=subset)\
        .applymap(color_shift, factory=factory, subset=subset) \
        .set_table_styles([{'selector': '.data', 'props': [('text-align', 'center')]},
                                       {'selector': '.col_heading', 'props': [('text-align', 'center')]},
                                       {'selector': '.col7', 'props': [('border-left',"2px solid black")]}
                                       ])

    if highlight_cover is True:

        def highlight(val):
            fill, req = val.split('/')
            if fill == req:
                return ''
            return 'color : red'

        subset = (df.index.tolist()[-len(factory.data.shifts):], df.columns[:-1])
        styler = styler.applymap(highlight, subset=subset)
    styler
    
    return styler
def color_shift(shift, factory):
    # cmap = ["yellow", "blue","red", "orange", "cyan"]
    cmap = plt.get_cmap("Set3") # https://matplotlib.org/2.0.2/examples/color/colormaps_reference.html
    if shift is None or shift == '':
        return 'background-color: white'
    # return f"background-color: {cmap(factory.shift_name_to_idx[shift])}"
    r,g,b = (round(255*val) for val in cmap.colors[factory.shift_name_to_idx[shift]])
    return f"background-color: rgb({r},{g},{b})"


def highlight_cell(cells, factory):
    def do_function(x):
        styles = []
        for i, _ in enumerate(x):
            txt = ""
            if (x.name, i) in cells:
                color = cells[(x.name, i)]
                txt = "background: "
                if isinstance(color, tuple): # rgb/rgba values
                    if len(color) == 3:
                        txt += f"rgb{color}"
                    elif len(color) == 4:
                        txt += f"rgba{color}"
                    else:
                        raise ValueError(f"Unknown color value {color}")
                else:
                    txt += color
            styles.append(txt)
        return styles
    return do_function


def highlight_cell_border(cells, factory):
    def do_function(x):
        return [f'border: 5px {cells[x.name, i]}'
                if (x.name, i) in cells else "" for i, _ in enumerate(x)]

    return do_function


def highlight_cover(covers, factory):
    nurses = factory.data.staff['name'].tolist()

    def do_function(x):
        borders = []
        for i, _ in enumerate(x):
            s = ''
            if i in covers:
                s += 'border-left: 5px solid red; border-right:5px solid red;'
                if x.name == nurses[0]:
                    s += 'border-top: 5px solid red;'
                if x.name.startswith("Cover"):
                    s += 'border-bottom: 5px solid red;'
            borders += [s]
        return borders

    return do_function


def highlight_row(nurse_id, work_windows, factory):
    nurses = factory.data.staff['name'].tolist()


    def do_function(x):
        borders = []

        if x.name not in nurses: return [''] * len(x)
        n_id = nurses.index(x.name)
        windows = [win for n,win in zip(nurse_id, work_windows) if n == n_id]
        for i, _ in enumerate(x):
            s = ''
            if any(i in w for w in windows):
                s = 'border-top: 5px solid red; border-bottom:5px solid red;'
                if any(i == w[0] for w in windows):
                    s += 'border-left: 5px solid red;'
                if any(i == w[-1] for w in windows):
                    s += 'border-right: 5px solid red;'
            borders += [s]
        return borders

    return do_function


def highlight_weekends(nurse_id, factory):
    nurses = factory.data.staff['name'].tolist()

    def do_function(x):
        borders = []
        names = [nurses[id] for id in nurse_id]
        for i, _ in enumerate(x):
            s = ''
            if i % 7 in [5, 6]:
                if x.name in names:
                    s = 'border-top: 5px solid indigo; border-bottom:5px solid indigo;'
                    if i % 7 == 5:
                        s += 'border-left: 5px solid indigo;'
                    if i % 7 == 6:
                        s += 'border-right: 5px solid indigo;'
            borders += [s]
        return borders

    return do_function


def highlight_changes(nurse_view, opt_sol, factory):
    nurses = factory.data.staff['name'].tolist()

    style = visualize(nurse_view.value(), factory)
    cells = {}
    for i in range(np.size(opt_sol, 0)):
        for j in range(np.size(opt_sol, 1)):
            if nurse_view[i, j].value() != opt_sol[i, j]:
                cells.update({(nurses[i], j): "solid lawngreen"})
    # cells = {(nurses[i], j): "solid lawngreen" for j in range(np.size(opt_sol,0)) for i in range(np.size(opt_sol,1)) if nurse_view[i,j].value() != opt_sol[i,j]}
    style.apply(highlight_cell_border(cells, factory), axis=1)
    return style


def visualize_constraints(subset, nurse_view, factory, do_clear=True):
    if do_clear:
        nurse_view.clear()
    style = visualize(nurse_view.value(), factory)
    cells = {(cons.cell[0], cons.cell[1]): cons.cell[2] for cons in subset if hasattr(cons, "cell")}
    style.apply(highlight_cell(cells, factory), axis=1)

    covers = {cons.cover for cons in subset if hasattr(cons, 'cover')}
    style.apply(highlight_cover(covers, factory), axis=1)
    nurse_id = {cons.nurse_id for cons in subset if hasattr(cons, 'nurse_id') and hasattr(cons, 'window')}
    windows = {cons.window for cons in subset if hasattr(cons, 'window')}
    style.apply(highlight_row(nurse_id, windows, factory), axis=1)
    nurse_id = {cons.nurse_id for cons in subset if hasattr(cons, 'nurse_id') and hasattr(cons, 'max_weekends')}
    style.apply(highlight_weekends(nurse_id, factory), axis=1)
    return style


def visualize_step(step, nurse_view, factory):
    E, S, N = step
    print(f"Propagating constraint: {next(iter(S))}")
    if any(len(vals) == 0 for vals in N.values()):
        # found UNSAT
        return visualize_constraints(S, nurse_view, factory=factory, do_clear=False)
    else:
        for v in E:
            if E[v] > N[v]:
                # derived something here
                assert len(N[v]) <= 1, "only allow assigments here..."
                # hacky way to find index
                r = int(v.name.split(",")[0].split('[')[1])
                c = int(v.name.split(",")[1].split(']')[0])
                nurse_view[r, c]._value = next(iter(N[v]))

    return visualize_constraints(S, nurse_view, factory=factory, do_clear=False)
