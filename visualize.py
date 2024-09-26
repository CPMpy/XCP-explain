
import pandas as pd
import numpy as np

import matplotlib.pyplot as plt

def visualize(sol, factory, highlight_cover=False):
    weeks = [f"Week {i + 1}" for i in range(factory.data.horizon // 7)]
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    nurses = factory.data.staff['name'].tolist()

    df = pd.DataFrame(sol,
                      columns=pd.MultiIndex.from_product((weeks, weekdays), names=("Week", "Day")),
                      index=factory.data.staff.name)


    total_minutes = df.map(lambda i : ([0] + list(factory.data.shifts.Length))[i] if i is not None else 0).sum(axis=1).astype(int)

    mapping = factory.idx_to_name
    df = df.map(lambda v: mapping[v] if v is not None else '')  # convert to shift names

    real_shifts = sorted(set(factory.shift_name_to_idx) - {"F"})
    total_shifts = pd.DataFrame(columns=pd.MultiIndex.from_product([["#Shifts"], real_shifts]), index=df.index)
    for shift_type in real_shifts:
        total_shifts[("#Shifts", shift_type)] = (df == shift_type).sum(axis=1)

    for shift_type in real_shifts:
        sums = (df == shift_type).sum()  # cover for each shift type
        req = factory.data.cover["Requirement"][factory.data.cover["ShiftID"] == shift_type]
        req.index = sums.index
        df.loc[f'Cover {shift_type}'] = sums.astype(str) + "/" + req.astype(str)


    df = pd.concat([df, total_shifts], axis=1)
    df["#Minutes"] = total_minutes
    df = df.fillna(0)
    df["#Shifts"] = df["#Shifts"].astype(int)
    df["#Minutes"] = df["#Minutes"].astype(int)


    subset = (df.index.tolist()[:-len(factory.data.shifts)], df.columns[:-(len(real_shifts)+1)])
    style = df.style.set_table_styles([{'selector': '.data', 'props': [('text-align', 'center')]},
                                       {'selector': '.col_heading', 'props': [('text-align', 'center')]},
                                       {'selector': '.col7', 'props': [('border-left',"2px solid black")]}])
    style = style.map(lambda v: 'border: 1px solid black', subset=subset)
    style = style.map(color_shift, factory=factory, subset=subset)  # color cells

    if highlight_cover is True:

        def highlight(val):
            fill, req = val.split('/')
            if fill == req:
                return ''
            return 'color : red'

        subset = (df.index.tolist()[-len(factory.data.shifts):], df.columns[:-1])
        style = style.map(highlight, subset=subset)

    return style

def color_shift(shift, factory):
    # cmap = ["yellow", "blue","red", "orange", "cyan"]
    cmap = plt.get_cmap("Set3") # https://matplotlib.org/2.0.2/examples/color/colormaps_reference.html
    if shift is None or shift == '':
        return 'background-color: white'
    # return f"background-color: {cmap(factory.shift_name_to_idx[shift])}"
    r,g,b = (round(255*val) for val in cmap.colors[factory.shift_name_to_idx[shift]])
    return f"background-color: rgb({r},{g},{b})"

def highlight_changes(new_sol, old_sol, factory):

    style = visualize(new_sol, factory)
    df_css = pd.DataFrame(new_sol != old_sol)
    df_css = df_css.reindex(index=style.index, columns=style.columns, fill_value="")
    print(df_css)
    df_css = df_css.map(lambda x : "border: 5px solid lawngreen" if x else "")
    print(df_css)
    return style.apply(apply_styles, styles=df_css, axis=None)



def visualize_constraints(constraints, nurse_view, factory):
    style = visualize(nurse_view.value(), factory)

    df_css = pd.DataFrame(index=style.index, columns=style.columns)
    df_css.fillna("", inplace=True)
    for cons in constraints:
        cons.visualize(df_css)
    return style.apply(apply_styles, styles=df_css, axis=None)

def visualize_step(step, nurse_view, factory):
    E, S, N = step
    print(f"Propagating constraint: {next(iter(S))}")
    if any(len(vals) == 0 for vals in N.values()):
        # found UNSAT
        return visualize_constraints(S, nurse_view, factory=factory)
    else:
        for v in E:
            if E[v] > N[v]:
                # derived something for this variable
                assert len(N[v]) <= 1, "only allow assigments here... (TODO? how to visualize negative facts?)"
                # hacky way to find index
                r = int(v.name.split(",")[0].split('[')[1])
                c = int(v.name.split(",")[1].split(']')[0])
                nurse_view[r, c]._value = next(iter(N[v]))

    return visualize_constraints(S, nurse_view, factory=factory)

