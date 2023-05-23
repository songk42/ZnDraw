from pydantic import BaseModel
import functools

import ase
import networkx as nx
import numpy as np
from ase.data.colors import jmol_colors
from ase.neighborlist import build_neighbor_list

from zndraw import shared

def _rgb2hex(data):
    r, g, b = np.array(data * 255, dtype=int)
    return "#%02x%02x%02x" % (r, g, b)


class ASEComputeBonds(BaseModel):
    
    def get_frame(self, step: int):
        atoms = shared.config.get_atoms(step=int(step))
        return {
            "particles": [
                {
                    "id": idx,
                    "x": atom.position[0],
                    "y": atom.position[1],
                    "z": atom.position[2],
                    "color": _rgb2hex(jmol_colors[atom.number]),
                    "radius": 0.25 * (2 - np.exp(-0.2 * atom.number)),
                    # "species": atom.species,
                }
                for idx, atom in enumerate(atoms)
            ],
            "bonds": self.get_bonds(atoms),
        }

    def get_bonds(self, atoms: ase.Atoms):
        atoms.pbc = False
        nl = build_neighbor_list(atoms, self_interaction=False)
        cm = nl.get_connectivity_matrix(sparse=False)
        G = nx.from_numpy_array(cm)
        return list(G.edges)
    
    def update_bond_order(self, particles: list[int], order: int):
        pass