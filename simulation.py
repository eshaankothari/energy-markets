import math

PRICE_CAP = 500
EPS = 1e-9

class Generator:
  def __init__(self, name, capacity, marginal_cost):
    self.name = name
    self.capacity = capacity
    self.marginal_cost = marginal_cost

class Dispatch:
  def __init__(self, dispatched_generators, price):
    self.dispatched_generators = dispatched_generators
    self.price = price


# assuming they bid their marginal cost
def clear_simple_market(generators, demand):
  if not isinstance(demand, (int, float)) or isinstance(demand, bool) \
      or not math.isfinite(demand) or demand < 0:
    raise ValueError(f"demand must be a finite number >= 0, got {demand!r}")
  if demand <= EPS:
    return Dispatch([], 0)

  sorted_generators = sorted(generators or [], key=lambda x: x.marginal_cost)
  dispatched_generators = []
  price = PRICE_CAP          # default = shortage; overwritten once demand is met
  for generator in sorted_generators:
    if generator.capacity <= 0:
      continue
    if generator.capacity < demand - EPS:
      dispatched_generators.append((generator.name, generator.capacity, generator.marginal_cost))
      demand -= generator.capacity
    else:
      dispatched_generators.append((generator.name, min(demand, generator.capacity), generator.marginal_cost))
      price = generator.marginal_cost
      break
  return Dispatch(dispatched_generators, price)


genco1 = Generator("genco1", 100, 100)
dispatch = clear_simple_market([genco1], 150)
print(dispatch.dispatched_generators, dispatch.price)

# print(genco1.capacity, genco1.marginal_cost)