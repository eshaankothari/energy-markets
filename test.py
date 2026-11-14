"""Tests for the original clear_market. Run: pytest test_original.py
Set MARKET_MODULE=<module> to run the same tests against another implementation."""
import importlib, os
import pytest
 
m = importlib.import_module(os.environ.get("MARKET_MODULE", "simulation"))
Generator, clear_market = m.Generator, m.clear_simple_market
CAP = getattr(m, "PRICE_CAP", None)

def run(gens, demand):
    d = clear_market(gens, demand)
    return d.dispatched_generators, d.price
 
 
def total(d):
    return sum(q for _, q, _ in d)
 
 
# ---- cases that should already work ----
def test_single_generator_exact():
    assert run([Generator("g1", 100, 100)], 100) == ([("g1", 100, 100)], 100)
 
def test_single_generator_cheaper_than_nothing_else():
    d, p = run([Generator("g1", 100, 7)], 100)
    assert p == 7
 
def test_does_not_mutate_input_order():
    gens = [Generator("b", 10, 2), Generator("a", 10, 1)]
    clear_market(gens, 5)
    assert [g.name for g in gens] == ["b", "a"]
 
 
# ---- partial dispatch of the marginal unit (bug: wrong quantity) ----
def test_single_generator_partial():
    assert run([Generator("g1", 100, 100)], 40) == ([("g1", 40, 100)], 100)
 
def test_two_generators_marginal_partial():
    gens = [Generator("a", 50, 10), Generator("b", 50, 20)]
    assert run(gens, 70) == ([("a", 50, 10), ("b", 20, 20)], 20)
 
def test_three_generators_unsorted_input():
    gens = [Generator("b", 50, 30), Generator("a", 50, 10), Generator("c", 50, 20)]
    assert run(gens, 120) == ([("a", 50, 10), ("c", 50, 20), ("b", 20, 30)], 30)
 
def test_generator_bigger_than_demand_is_not_fully_dispatched():
    # bug: capacity compared to total demand, not remaining demand
    gens = [Generator("a", 60, 10), Generator("b", 60, 20)]
    d, p = run(gens, 100)
    assert d == [("a", 60, 10), ("b", 40, 20)] and p == 20
 
def test_dispatch_never_exceeds_demand():
    gens = [Generator("a", 50, 1), Generator("b", 50, 2), Generator("c", 50, 3)]
    for demand in (1, 49, 50, 51, 99, 100, 101, 149):
        d, _ = run(gens, demand)
        assert total(d) == pytest.approx(demand)
 
def test_no_negative_or_zero_quantities():
    gens = [Generator("a", 50, 1), Generator("b", 50, 2)]
    for demand in (10, 50, 75, 100):
        d, _ = run(gens, demand)
        assert all(q > 0 for _, q, _ in d)
 
 
# ---- boundaries ----
def test_demand_equals_cumulative_capacity():
    gens = [Generator("a", 50, 10), Generator("b", 50, 20)]
    d, p = run(gens, 50)
    assert d == [("a", 50, 10)] and p == 10
 
def test_demand_equals_total_capacity():
    gens = [Generator("a", 50, 10), Generator("b", 50, 20)]
    d, p = run(gens, 100)
    assert d == [("a", 50, 10), ("b", 50, 20)] and p == 20
 
def test_just_above_boundary():
    gens = [Generator("a", 50, 10), Generator("b", 50, 20)]
    d, p = run(gens, 50.5)
    assert d[1] == ("b", pytest.approx(0.5), 20) and p == 20
 
 
# ---- zero / empty ----
def test_zero_demand():
    d, p = run([Generator("a", 50, 10)], 0)
    assert d == [] and p == 0
 
def test_empty_generators_zero_demand():
    assert run([], 0) == ([], 0)
 
def test_zero_capacity_generator_never_sets_price():
    gens = [Generator("z", 0, 1), Generator("a", 50, 10)]
    d, p = run(gens, 20)
    assert d == [("a", 20, 10)] and p == 10
 
 
# ---- shortage: demand > total capacity (original silently returns price 0) ----
def test_shortage_dispatches_all_capacity():
    gens = [Generator("a", 50, 10), Generator("b", 50, 20)]
    d, _ = run(gens, 130)
    assert total(d) == 100
 
def test_shortage_price_is_not_zero():
    gens = [Generator("a", 50, 10), Generator("b", 50, 20)]
    _, p = run(gens, 130)
    assert p == CAP   # highest bid; a silent 0 would signal "free power"
 
def test_no_generators_positive_demand_does_not_crash():
    d, _ = run([], 10)
    assert d == []
 
 
# ---- costs ----
def test_ties_in_cost_stable_and_correct():
    gens = [Generator("a", 10, 5), Generator("b", 10, 5)]
    d, p = run(gens, 15)
    assert d == [("a", 10, 5), ("b", 5, 5)] and p == 5
 
def test_negative_marginal_cost():
    gens = [Generator("wind", 10, -5), Generator("gas", 50, 10)]
    assert run(gens, 5) == ([("wind", 5, -5)], -5)
 
def test_float_demand():
    d, p = run([Generator("a", 0.1, 1), Generator("b", 0.2, 2)], 0.15)
    assert d[1][1] == pytest.approx(0.05) and p == 2
 
 
# ---- invalid input: should not silently produce garbage ----
@pytest.mark.parametrize("bad", [-1, float("nan")])
def test_invalid_demand_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        clear_market([Generator("a", 10, 1)], bad)