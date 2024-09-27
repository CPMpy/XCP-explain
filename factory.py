import numpy as np
import xmltodict

import read_data
from read_data import SchedulingProblem
import cpmpy as cp

FREE = 0

class NurseSchedulingFactory:

    def __init__(self, data:SchedulingProblem):

        self.data = data
        self.n_types = len(data.shifts)
        self.n_nurses = len(data.staff)
        self.weekends = [(i - 1, i) for i in range(data.horizon) if i != 0 and (i + 1) % 7 == 0]
        self.shift_name_to_idx = {name: idx + 1 for idx, (name, _) in enumerate(data.shifts.iterrows())}
        self.idx_to_name = ["F"] + [key for key in self.shift_name_to_idx]
        self.shift_name_to_idx.update({"F": 0})

        self.nurse_map = list(self.data.staff["# ID"])

        weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self.days = [f"{weekdays[i % 7]} {1 + (i // 7)}" for i in range(data.horizon)]

        # decision vars
        self.nurse_view = cp.intvar(0, self.n_types, shape=(self.n_nurses, data.horizon), name="roster")

        self.slack_over = cp.intvar(0, self.n_nurses, shape=(len(self.days), self.n_types))
        self.slack_under = cp.intvar(0, self.n_nurses, shape=(len(self.days), self.n_types))

        # some visualization stuff
        self.day_off_color = "lightgreen"
        self.on_request_color = (183, 119, 41)  # copper-ish
        self.off_request_color = (212, 175, 55)  # gold-ish


    def get_hard_constraints(self):
        model = cp.Model()
        model += self.shift_rotation()
        model += self.max_shifts()
        model += self.max_minutes()
        model += self.min_minutes()
        model += self.max_consecutive()
        model += self.min_consecutive()
        model += self.weekend_shifts()
        model += self.days_off()
        model += self.min_consecutive_off()

        return model

    def get_optimization_model(self):

        cons_on, penalty_on = self.shift_on_requests(formulation="soft")
        cons_off, penalty_off = self.shift_off_requests(formulation="soft")
        cons_cover, penalty_cover = self.cover(formulation="soft")

        model = self.get_hard_constraints()
        model += [cons_on, cons_off, cons_cover]
        obj_func = penalty_on + penalty_off + penalty_cover
        model.minimize(obj_func)

        return model, self.nurse_view

    def get_decision_model(self):

        model = self.get_hard_constraints()
        cons_on, penalty_on = self.shift_on_requests(formulation="hard")
        cons_off, penalty_off = self.shift_off_requests(formulation="hard")
        cons_cover, penalty_cover = self.cover(formulation="hard")

        model += [cons_on, cons_off, cons_cover]
        obj_func = penalty_on + penalty_off + penalty_cover
        model.minimize(obj_func)

        return model, self.nurse_view

    def get_slack_model(self):

        model = self.get_hard_constraints()

        cons_on, penalty_on = self.shift_on_requests(formulation="hard")
        cons_off, penalty_off = self.shift_off_requests(formulation="hard")
        cons_cover, penalty_cover = self.cover(formulation="soft")

        model += [cons_on, cons_off, cons_cover]

        return model, self.nurse_view, self.slack_over, self.slack_under

    def shift_rotation(self):
        """
        Shifts which cannot follow the shift on the previous day.
        This constraint always assumes that the last day of the previous planning period was a day off and
            the first day of the next planning horizon is a day off.
        """
        constraints = []
        for t, (_, shift) in enumerate(self.data.shifts.iterrows()):
            cannot_follow = [self.shift_name_to_idx[name] for name in shift["cannot follow"] if name != '']
            for other_shift in cannot_follow:
                for n in range(self.n_nurses):
                    for d in range(self.data.horizon - 1):
                        cons = (self.nurse_view[n, d] == t + 1).implies(self.nurse_view[n, d + 1] != other_shift)
                        cons.set_description(
                            f"Shift {other_shift} cannot follow {cannot_follow} for {self.data.staff.iloc[n]['name']}")
                        constraints.append(cons)
                        cons.visualize = lambda style : None
        return constraints

    def max_shifts(self):
        """
        The maximum number of shifts of each type that can be assigned to each employee.
        """

        def get_visualizer(nurse_idx, shift_id):
            def visualize(styler):
                styler[("#Shifts", shift_id)].iloc[nurse_idx] += f'border: 5px dotted red;'
            return visualize

        constraints = []
        for _, nurse in self.data.staff.iterrows():
            n = self.nurse_map.index(nurse['# ID'])
            for shift_id, shift in self.data.shifts.iterrows():
                n_shifts = cp.Count(self.nurse_view[n], self.shift_name_to_idx[shift_id])
                max_shifts = nurse[f"max_shifts_{shift_id}"]
                cons = n_shifts <= max_shifts
                cons.set_description(f"{nurse['name']} can work at most {max_shifts} {shift_id}-shifts")
                cons.visualize = get_visualizer(n, shift_id)
                constraints.append(cons)

        return constraints

    def max_minutes(self):
        """
        The maximum amount of total time in minutes that can be assigned to each employee.
        """

        def get_visualizer(nurse_idx):
            def visualize(styler):
                styler.iloc[nurse_idx, -1] += 'border: 5px dotted red;'
            return visualize

        constraints = []
        shift_length = cp.cpm_array([0] + [l for l in self.data.shifts.Length])
        for _, nurse in self.data.staff.iterrows():
            n = self.nurse_map.index(nurse['# ID'])
            time_worked = cp.sum(shift_length[t] for t in self.nurse_view[n])
            constraint = time_worked <= nurse["MaxTotalMinutes"]

            constraint.set_description(f"{nurse['name']} cannot work more than {nurse['MaxTotalMinutes']}min")
            constraint.visualize = get_visualizer(n)
            constraints.append(constraint)
        return constraints

    def min_minutes(self):
        """
        The maximum amount of total time in minutes that can be assigned to each employee.
        """
        def get_visualizer(nurse_idx):
            def visualize(styler):
                styler.iloc[nurse_idx, -1] += 'border: 5px dotted green;'
            return visualize

        constraints = []
        shift_length = cp.cpm_array([0] + [l for l in self.data.shifts.Length])
        for _, nurse in self.data.staff.iterrows():
            n = self.nurse_map.index(nurse["# ID"])
            time_worked = cp.sum(shift_length[t] for t in self.nurse_view[n])
            constraint = time_worked >= nurse["MinTotalMinutes"]

            constraint.set_description(f"{nurse['name']} should work at least {nurse['MinTotalMinutes']}min")
            constraint.visualize = get_visualizer(n)
            constraints.append(constraint)
        return constraints


    def max_consecutive(self):
        """
        The maximum number of consecutive shifts that can be worked before having a day off.
        This constraint always assumes that the last day of the previous planning period was a day off
            and the first day of the next planning period is a day off.
        """
        def get_visualizer(nurse_idx, window):
            def visualize(styler):
                styler.iloc[nurse_idx, window[0]] += "border-left: 5px solid red;"
                styler.iloc[nurse_idx, window[-1]] += "border-right: 5px solid red;"
                for day in window:
                    styler.iloc[nurse_idx, day] += "border-top: 5px solid red; border-bottom: 5px solid red;"
            return visualize


        constraints = []
        for _, nurse in self.data.staff.iterrows():
            n = self.nurse_map.index(nurse['# ID'])
            max_days = nurse['MaxConsecutiveShifts']
            for i in range(self.data.horizon - max_days):
                window = self.nurse_view[n][i:i+max_days+1]
                constraint = cp.Count(window, 0) >= 1
                constraint.set_description(f"{nurse['name']} can work at most {max_days} days before having a day off")
                constraint.visualize = get_visualizer(n, list(range(i,i+max_days+1)))
                constraints.append(constraint)

        return constraints


    def min_consecutive(self):
        """
            The minimum number of shifts that must be worked before having a day off.
            This constraint always assumes that there are an infinite number of consecutive shifts
                assigned at the end of the previous planning period and at the start of the next planning period.
        """

        def get_visualizer(nurse_idx, window):
            def visualize(styler):
                styler.iloc[nurse_idx, window[0]] += "border-left: 5px dotted teal;"
                styler.iloc[nurse_idx, window[-1]] += "border-right: 5px dotted teal;"
                for day in window:
                    styler.iloc[nurse_idx, day] += "border-top: 5px dotted teal; border-bottom: 5px dotted teal;"

            return visualize

        constraints = []
        for _, nurse in self.data.staff.iterrows():
            n = self.nurse_map.index(nurse['# ID'])
            min_days = nurse["MinConsecutiveShifts"]
            nurse_shifts = self.nurse_view[n]
            for i, shift in enumerate(nurse_shifts):
                if i == 0: # first shift can never be start of working period
                    continue

                is_start_of_working_period = (shift != FREE) & (nurse_shifts[i-1] == FREE)

                constraint = is_start_of_working_period.implies(cp.all(nurse_shifts[i:i+min_days] != FREE))
                constraint.set_description(f"{nurse['name']} should work at least {min_days} days before having a day off")
                constraint.visualize = get_visualizer(n, list(range(i,i+min_days)))
                constraints.append(constraint)

        return constraints


    def weekend_shifts(self):
        """
            Max nb of working weekends for each nurse.
            A weekend is defined as being worked if there is a shift on the Saturday or the Sunday.
        """
        def get_visualizer(nurse_idx):
            def visualize(styler):
                for sat, sun in self.weekends:
                    styler.iloc[nurse_idx, sat] += "border-left: 5px solid indigo; border-top: 5px solid indigo; border-bottom: 5px solid indigo;"
                    styler.iloc[nurse_idx, sun] += "border-right: 5px solid indigo; border-top: 5px solid indigo; border-bottom: 5px solid indigo;"
            return visualize

        constraints = []
        for _, nurse in self.data.staff.iterrows():
            n = self.nurse_map.index(nurse['# ID'])
            max_weekends = nurse['MaxWeekends']
            shifts = self.nurse_view[n]
            n_weekends = cp.sum([(shifts[sat] != FREE) | (shifts[sun] != FREE) for sat,sun in self.weekends])
            constraint = n_weekends <= max_weekends
            constraint.set_description(f"{nurse['name']} should work at most {max_weekends} weekends")
            constraint.visualize = get_visualizer(n)
            constraints.append(constraint)
        return constraints


    def days_off(self):

        def get_visualizer(nurse_idx, day):
            def visualize(styler):
                styler.iloc[nurse_idx, day] += "background-color:lightgreen;"
            return visualize

        constraints = []
        for (_, holiday) in self.data.days_off.iterrows():
            n = self.nurse_map.index(holiday['EmployeeID'])
            constraint = self.nurse_view[n, holiday['DayIndex']] == FREE
            constraint.set_description(f"{self.data.staff.iloc[n]['name']} has a day off on {self.days[holiday['DayIndex']]}")
            constraint.visualize = get_visualizer(n, holiday['DayIndex'])
            constraints.append(constraint)

        return constraints


    def min_consecutive_off(self):
        """
        The minimum number of consecutive days off that must be assigned before assigning a shift.
        This constraint always assumes that there are an infinite number of consecutive days off assigned
            at the end of the previous planning period and at the start of the next planning period.
        """

        def get_visualizer(nurse_idx, window):
            def visualize(styler):
                styler.iloc[nurse_idx, window[0]] += "border-left: 5px dotted lightgreen;"
                styler.iloc[nurse_idx, window[-1]] += "border-right: 5px dotted lightgreen;"
                for day in window:
                    styler.iloc[nurse_idx, day] += "border-top: 5px dotted lightgreen; border-bottom: 5px dotted lightgreen;"

            return visualize

        constraints = []
        for _, nurse in self.data.staff.iterrows():
            n = self.nurse_map.index(nurse['# ID'])
            min_days = nurse["MinConsecutiveDaysOff"]
            nurse_shifts = self.nurse_view[n]
            nurse_constraint = []
            for i, shift in enumerate(nurse_shifts):
                if i == 0: # can never be the first of a free period
                    continue

                is_start_of_free_period = (shift == FREE) & (nurse_shifts[i - 1] != FREE)

                constraint = is_start_of_free_period.implies(cp.all(nurse_shifts[i:i+min_days] == FREE))
                constraint.set_description(f"{nurse['name']} should have at least {min_days} consecutive days off")
                constraint.visualize = get_visualizer(n, list(range(i, i+min_days)))
                constraints.append(constraint)

        return constraints

    def shift_on_requests(self, formulation="soft"):
        """
            If the specified shift is not assigned to the specified employee on the specified day
                then the solution's penalty is the specified weight value.

            :param: decision: If False, returns a numerical expression with the penalties
                               if True, returns a set of constraints requiring the request to be satisfied
        """

        def get_visualizer(nurse_idx, day):
            def visualize(styler):
                styler.iloc[nurse_idx, day] += "background-color:rgb(183, 119, 41);"
            return visualize


        constraints = []
        penalty = []
        for _, request in self.data.shift_on.iterrows():
            n = self.nurse_map.index(request['# EmployeeID'])
            shift = self.shift_name_to_idx[request['ShiftID']]
            day = request['Day']
            if formulation == "hard":
                constraint = self.nurse_view[n, day] == shift
                constraint.set_description(f"{self.data.staff.iloc[n]['name']} requests to work shift {self.idx_to_name[shift]}")
                constraint.visualize = get_visualizer(n, day)
                constraints.append(constraint)
            else: # penalty
                penalty.append(request['Weight'] * (self.nurse_view[n, day] != shift))

        return constraints, cp.sum(penalty)


    def shift_off_requests(self, formulation="soft"):
        """
            If the specified shift is assigned to the specified employee on the specified day
                then the solution's penalty is the weight value.
        """
        def get_visualizer(nurse_idx, day):
            def visualize(styler):
                styler.iloc[nurse_idx, day] += "background-color: rgb(212,175,55);"
            return visualize

        constraints = []
        penalty = []
        for _, request in self.data.shift_off.iterrows():
            n = self.nurse_map.index(request['# EmployeeID'])
            shift = self.shift_name_to_idx[request['ShiftID']]
            day = request['Day']
            if formulation == "hard":
                constraint = self.nurse_view[n, day] != shift
                constraint.set_description(f"{self.data.staff.iloc[n]['name']} requests to work shift {self.idx_to_name[shift]}")
                constraint.visualize = get_visualizer(n, day)
                constraints.append(constraint)
            else:  # penalty
                penalty.append(request['Weight'] * (self.nurse_view[n, day] == shift))

        return constraints, cp.sum(penalty)

    def cover(self, formulation="soft"):
        """
        If the required number of staff on the specified day for the specified shift is not assigned
            then it is a soft constraint violation

        :param: formulation: the formulation used. Can be any of "penalty", "slack", or "hard"
                                - slack:
                                - hard:
        """
        def get_visualizer(day, shift):

            def visualize(styler):
                styler.iloc[0, day] += "border-top: solid red;"
                for n in range(self.n_nurses):
                    styler.iloc[n, day] += "border-left: solid red; border-right: solid red;"
                styler.iloc[n + shift, day] += "border-bottom: solid red; border-left: solid red; border-right: solid red;"
            return visualize


        constraints = []
        penalties = []
        for _, cover in self.data.cover.iterrows():
            shift = self.shift_name_to_idx[cover["ShiftID"]]
            requirement = cover['Requirement']
            day =  cover['# Day']
            nb_nurses = cp.Count(self.nurse_view[:, day], shift)
            if formulation == "soft":
                slack_over = self.slack_over[day, shift-1]
                slack_under = self.slack_under[day, shift-1]
                penalties += [cover["Weight for over"] * slack_over, cover["Weight for under"] * slack_under]
            elif formulation == "hard":
                slack_over, slack_under = 0,0
            else:
                raise ValueError(f"Unexpected formulation for constraint. Should be 'penalty', 'slack', or 'hard' but got {formulation}")

            expr = nb_nurses + (-slack_over) + slack_under == requirement
            expr.visualize = get_visualizer(day, shift)
            expr.set_description(
                f"Shift {cover['ShiftID']} on {self.days[day]} must be covered by {requirement} nurses out of {len(self.nurse_view)}")
            constraints.append(expr)

        return constraints, cp.sum(penalties)

def is_not_none(*args):
    if any(a is None for a in args):
        return False
    else:
        return True