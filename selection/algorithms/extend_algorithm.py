import logging
import time

from selection.index import Index
from selection.selection_algorithm import DEFAULT_PARAMETER_VALUES, SelectionAlgorithm
from selection.utils import b_to_mb, mb_to_b

# budget_MB: The algorithm can utilize the specified storage budget in MB.
# max_index_width: The number of columns an index can contain at maximum.
# min_cost_improvement: The value of the relative improvement that must be realized by a
#                       new configuration to be selected.
# The algorithm stops if either the budget is exceeded or no further beneficial
# configurations can be found.
DEFAULT_PARAMETERS = {
    "budget_MB": DEFAULT_PARAMETER_VALUES["budget_MB"],
    "max_index_width": DEFAULT_PARAMETER_VALUES["max_index_width"],
    "min_cost_improvement": 1.003,
    "selection_timeout_seconds": None,
}


# This algorithm is a reimplementation of the Extend heuristic published by Schlosser,
# Kossmann, and Boissier in 2019.
# Details can be found in the original paper:
# Rainer Schlosser, Jan Kossmann, Martin Boissier: Efficient Scalable
# Multi-attribute Index Selection Using Recursive Strategies. ICDE 2019: 1238-1249
class ExtendAlgorithm(SelectionAlgorithm):
    def __init__(self, database_connector, parameters=None):
        if parameters is None:
            parameters = {}
        SelectionAlgorithm.__init__(
            self, database_connector, parameters, DEFAULT_PARAMETERS
        )
        self.budget = mb_to_b(self.parameters["budget_MB"])
        self.max_index_width = self.parameters["max_index_width"]
        self.workload = None
        self.min_cost_improvement = self.parameters["min_cost_improvement"]
        self.selection_timeout_seconds = self.parameters["selection_timeout_seconds"]

    def _calculate_best_indexes(self, workload):
        logging.info("Calculating best indexes Extend")
        self.workload = workload
        single_attribute_index_candidates = self.workload.potential_indexes()
        extension_attribute_candidates = single_attribute_index_candidates.copy()

        index_combination = []
        index_combination_size = 0
        best = {"combination": [], "benefit_to_size_ratio": 0, "cost": None}
        _start = time.time()
        _iter = 0

        current_cost = self.cost_evaluation.calculate_cost(
            self.workload, index_combination, store_size=True
        )
        self.initial_cost = current_cost
        logging.info(f"Extend: initial workload cost = {current_cost:.2f}")

        _deadline = (
            _start + self.selection_timeout_seconds
            if self.selection_timeout_seconds
            else None
        )
        while True:
            elapsed = time.time() - _start
            if _deadline and time.time() > _deadline:
                logging.info(
                    f"Extend: selection timeout ({self.selection_timeout_seconds}s) reached "
                    f"after {elapsed:.0f}s, returning {len(index_combination)} indexes."
                )
                break

            single_attribute_index_candidates = self._get_candidates_within_budget(
                index_combination_size, single_attribute_index_candidates
            )
            n_single = sum(1 for c in single_attribute_index_candidates if c not in index_combination)
            n_ext = sum(
                1 for attr in extension_attribute_candidates
                for idx in index_combination
                if len(idx.columns) < self.max_index_width and idx.appendable_by(attr)
            )
            logging.info(
                f"Extend iter {_iter}: elapsed={elapsed:.0f}s  "
                f"candidates={n_single} single + {n_ext} extensions  "
                f"current combo ({len(index_combination)}): "
                f"[{', '.join(str(i) for i in index_combination)}]"
            )

            for candidate in single_attribute_index_candidates:
                if candidate not in index_combination:
                    logging.debug(f"  evaluating single: {candidate}")
                    self._evaluate_combination(
                        index_combination + [candidate], best, current_cost
                    )

            for attribute in extension_attribute_candidates:
                self._attach_to_indexes(index_combination, attribute, best, current_cost)

            if best["benefit_to_size_ratio"] <= 0:
                logging.info(
                    f"Extend: no further improvements at iter {_iter} "
                    f"(elapsed={elapsed:.0f}s). Done."
                )
                break

            added = best["combination"][-1]
            improvement_pct = (1 - best["cost"] / current_cost) * 100
            total_improvement_pct = (1 - best["cost"] / self.initial_cost) * 100
            index_combination = best["combination"]
            index_combination_size = sum(
                index.estimated_size for index in index_combination
            )
            logging.info(
                f"Extend iter {_iter}: added {added}  "
                f"cost ↓{improvement_pct:.2f}% (total ↓{total_improvement_pct:.2f}%)  "
                f"combo size {len(index_combination)}  "
                f"storage {b_to_mb(index_combination_size):.1f}MB"
            )

            best["benefit_to_size_ratio"] = 0
            current_cost = best["cost"]
            _iter += 1

        return index_combination

    def _attach_to_indexes(self, index_combination, attribute, best, current_cost):
        assert (
            attribute.is_single_column() is True
        ), "Attach to indexes called with multi column index"

        for position, index in enumerate(index_combination):
            if len(index.columns) >= self.max_index_width:
                continue
            if index.appendable_by(attribute):
                new_index = Index(index.columns + attribute.columns)
                if new_index in index_combination:
                    continue
                new_combination = index_combination.copy()
                # We don't replace, but del and append to keep track of the append order
                del new_combination[position]
                new_combination.append(new_index)
                logging.debug(f"  evaluating extension: {new_index}")
                self._evaluate_combination(
                    new_combination,
                    best,
                    current_cost,
                    index_combination[position].estimated_size,
                )

    def _get_candidates_within_budget(self, index_combination_size, candidates):
        new_candidates = []
        for candidate in candidates:
            if (candidate.estimated_size is None) or (
                candidate.estimated_size + index_combination_size <= self.budget
            ):
                new_candidates.append(candidate)
        return new_candidates

    def _evaluate_combination(
        self, index_combination, best, current_cost, old_index_size=0
    ):
        cost = self.cost_evaluation.calculate_cost(
            self.workload, index_combination, store_size=True
        )
        if (cost * self.min_cost_improvement) >= current_cost:
            return
        benefit = current_cost - cost
        new_index = index_combination[-1]
        new_index_size_difference = new_index.estimated_size - old_index_size
        if new_index_size_difference == 0:
            return

        ratio = benefit / new_index_size_difference

        total_size = sum(index.estimated_size for index in index_combination)

        if ratio > best["benefit_to_size_ratio"] and total_size <= self.budget:
            logging.debug(
                f"new best cost and size: {cost}\t" f"{b_to_mb(total_size):.2f}MB"
            )
            best["combination"] = index_combination
            best["benefit_to_size_ratio"] = ratio
            best["cost"] = cost
