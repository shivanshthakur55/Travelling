"""
ga_tsp.py

Genetic Algorithm solver for the open Travelling Salesman Problem.

Constraints
───────────
- Start node is PINNED (always first in route).
- Route is OPEN (does not loop back to start).
- GA only permutes the delivery nodes.

Algorithm
─────────
- Representation  : permutation of delivery indices
- Selection       : tournament selection (k=5)
- Crossover       : Ordered Crossover (OX)
- Mutation        : swap mutation
- Elitism         : top 2 individuals always survive
"""

import random
import math


# ─────────────────────────────────────────────
# Default hyper-parameters
# ─────────────────────────────────────────────

POP_SIZE    = 200
GENERATIONS = 500
MUTATION_RATE = 0.02
TOURNAMENT_K  = 5
ELITE_SIZE    = 2


# ─────────────────────────────────────────────
# Fitness
# ─────────────────────────────────────────────

def route_distance(route: list[int], matrix: list[list[float]]) -> float:
    """Total distance of a route (list of node indices)."""
    return sum(matrix[route[i]][route[i + 1]] for i in range(len(route) - 1))


# ─────────────────────────────────────────────
# GA operators
# ─────────────────────────────────────────────

def _tournament_select(population: list[list[int]],
                       fitnesses: list[float],
                       k: int = TOURNAMENT_K) -> list[int]:
    """Return the best individual from a random sample of k."""
    sample = random.sample(range(len(population)), k)
    best = min(sample, key=lambda idx: fitnesses[idx])
    return population[best][:]


def _ordered_crossover(parent1: list[int], parent2: list[int]) -> list[int]:
    """OX crossover: preserves relative order from parent2."""
    size = len(parent1)
    a, b = sorted(random.sample(range(size), 2))

    child = [None] * size
    child[a:b] = parent1[a:b]

    fill = [x for x in parent2 if x not in child]
    idx = 0
    for i in range(size):
        if child[i] is None:
            child[i] = fill[idx]
            idx += 1

    return child


def _swap_mutate(route: list[int], rate: float = MUTATION_RATE) -> list[int]:
    """Randomly swap two positions with probability `rate`."""
    route = route[:]
    for i in range(len(route)):
        if random.random() < rate:
            j = random.randint(0, len(route) - 1)
            route[i], route[j] = route[j], route[i]
    return route


# ─────────────────────────────────────────────
# Solver
# ─────────────────────────────────────────────

def solve_tsp(distance_matrix: list[list[float]],
              start_index: int = 0,
              pop_size: int = POP_SIZE,
              generations: int = GENERATIONS,
              mutation_rate: float = MUTATION_RATE) -> list[int]:
    """
    Solve the open TSP using a Genetic Algorithm.

    Parameters
    ──────────
    distance_matrix : NxN matrix of distances
    start_index     : index of the warehouse/start node (always first)
    pop_size        : GA population size
    generations     : number of GA generations
    mutation_rate   : per-gene mutation probability

    Returns
    ───────
    Ordered list of node indices starting from `start_index`,
    representing the near-optimal open route.
    """
    n = len(distance_matrix)

    if n <= 1:
        return [start_index]

    # Delivery nodes = all nodes except the start
    delivery_nodes = [i for i in range(n) if i != start_index]

    if not delivery_nodes:
        return [start_index]

    # With only 1 delivery node the route is trivial — no GA needed
    if len(delivery_nodes) == 1:
        print("  Only 1 delivery stop — route is direct, no optimisation needed.")
        return [start_index, delivery_nodes[0]]

    # ── Initialise population ────────────────
    population = [
        random.sample(delivery_nodes, len(delivery_nodes))
        for _ in range(pop_size)
    ]

    def full_route(perm):
        return [start_index] + perm

    def fitness(perm):
        return route_distance(full_route(perm), distance_matrix)

    best_perm = min(population, key=fitness)
    best_dist = fitness(best_perm)

    # ── Evolve ───────────────────────────────
    for gen in range(generations):
        fitnesses = [fitness(ind) for ind in population]

        # Elitism: preserve top individuals
        sorted_pop = sorted(zip(fitnesses, population), key=lambda x: x[0])
        new_population = [ind for _, ind in sorted_pop[:ELITE_SIZE]]

        # Fill the rest via selection + crossover + mutation
        while len(new_population) < pop_size:
            p1 = _tournament_select(population, fitnesses)
            p2 = _tournament_select(population, fitnesses)
            child = _ordered_crossover(p1, p2)
            child = _swap_mutate(child, mutation_rate)
            new_population.append(child)

        population = new_population

        # Track best
        gen_best = sorted_pop[0]
        if gen_best[0] < best_dist:
            best_dist = gen_best[0]
            best_perm = gen_best[1]

        # Progress every 100 generations
        if (gen + 1) % 100 == 0:
            print(f"  Gen {gen+1:4d}/{generations} — "
                  f"best distance: {best_dist/1000:.2f} km")

    print(f"  GA complete — optimal distance: {best_dist/1000:.2f} km")
    return full_route(best_perm)
