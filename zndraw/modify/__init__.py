import abc
import enum
import logging
import time
import typing as t

import ase
import numpy as np
from ase.data import chemical_symbols
from pydantic import Field, computed_field

from zndraw.base import Extension, MethodsCollection

try:
    from zndraw.modify import extras  # noqa: F401
except ImportError:
    # mdanalysis is not installed
    pass

# if t.TYPE_CHECKING:
from zndraw.zndraw import ZnDraw


log = logging.getLogger("zndraw")

Symbols = enum.Enum("Symbols", {symbol: symbol for symbol in chemical_symbols})


class UpdateScene(Extension, abc.ABC):
    @abc.abstractmethod
    def run(self, vis: "ZnDraw", timeout: float, **kwargs) -> None:
        """Method called when running the modifier."""
        pass

    def apply_selection(
        self, atom_ids: list[int], atoms: ase.Atoms
    ) -> t.Tuple[ase.Atoms, ase.Atoms]:
        """Split the atoms object into the selected and remaining atoms."""
        atoms_selected = atoms[atom_ids]
        atoms_remaining_ids = [x for x in range(len(atoms)) if x not in atom_ids]
        if len(atoms_remaining_ids) > 0:
            atoms_remaining = atoms[atoms_remaining_ids]
        else:
            atoms_remaining = ase.Atoms()
        return atoms_selected, atoms_remaining


class Connect(UpdateScene):
    """Create guiding curve between selected atoms."""

    def run(self, vis: "ZnDraw", **kwargs) -> None:
        atoms = vis.atoms
        atom_ids = vis.selection
        atom_positions = vis.atoms.get_positions()
        camera_position = np.array(vis.camera["position"])[None, :]  # 1,3

        new_points = atom_positions[atom_ids]  # N, 3
        radii: np.ndarray = atoms.arrays["radii"][atom_ids][:, None]
        direction = camera_position - new_points
        direction /= np.linalg.norm(direction, axis=1, keepdims=True)
        new_points += direction * radii

        vis.points = new_points
        vis.selection = []


class Rotate(UpdateScene):
    """Rotate the selected atoms around a the line (2 points only)."""

    angle: float = Field(90, le=360, ge=0, description="Angle in degrees")
    direction: t.Literal["left", "right"] = Field(
        "left", description="Direction of rotation"
    )
    steps: int = Field(
        30, ge=1, description="Number of steps to take to complete the rotation"
    )
    sleep: float = Field(0.1, ge=0, description="Sleep time between steps")

    def run(self, vis: "ZnDraw", **kwargs) -> None:
        # split atoms object into the selected from atoms_ids and the remaining
        if len(vis) > vis.step + 1:
            del vis[vis.step + 1 :]

        points = vis.points
        atom_ids = vis.selection
        atoms = vis.atoms
        if len(points) != 2:
            raise ValueError("Please draw exactly 2 points to rotate around.")

        angle = self.angle if self.direction == "left" else -self.angle
        angle = angle / self.steps

        atoms_selected, atoms_remaining = self.apply_selection(atom_ids, atoms)
        # create a vector from the two points
        vector = points[1] - points[0]
        for _ in range(self.steps):
            # rotate the selected atoms around the vector
            atoms_selected.rotate(angle, vector, center=points[0])
            # update the positions of the selected atoms
            atoms.positions[atom_ids] = atoms_selected.positions
            vis.append(atoms)
            time.sleep(self.sleep)


class Delete(UpdateScene):
    """Delete the selected atoms."""

    def run(self, vis: "ZnDraw", **kwargs) -> None:
        atom_ids = vis.selection
        atoms = vis.atoms

        if len(vis) > vis.step + 1:
            del vis[vis.step + 1 :]
        vis.log(f"Deleting atoms {atom_ids}")
        if len(atom_ids) == len(atoms):
            vis.append(ase.Atoms())
        else:
            for idx, atom_id in enumerate(sorted(atom_ids)):
                atoms.pop(atom_id - idx)  # we remove the atom and shift the index
            if hasattr(atoms, "connectivity"):
                del atoms.connectivity
        vis.append(atoms)
        vis.selection = []
        vis.step += 1


class Translate(UpdateScene):
    """Move the selected atoms along the line."""

    steps: int = Field(10, ge=1)

    def run(self, vis: "ZnDraw", **kwargs) -> None:
        if len(vis) > vis.step + 1:
            del vis[vis.step + 1 :]

        if self.steps > len(vis.segments):
            raise ValueError(
                "The number of steps must be less than the number of segments. You can add more points to increase the number of segments."
            )

        segments = vis.segments
        atoms = vis.atoms
        selection = np.array(vis.selection)

        for idx in range(self.steps):
            end_idx = int((idx + 1) * (len(segments) - 1) / self.steps)
            tmp_atoms = atoms.copy()
            vector = segments[end_idx] - segments[0]
            positions = tmp_atoms.positions
            positions[selection] += vector
            tmp_atoms.positions = positions
            vis.append(tmp_atoms)


class Duplicate(UpdateScene):
    x: float = Field(0.5, le=5, ge=0)
    y: float = Field(0.5, le=5, ge=0)
    z: float = Field(0.5, le=5, ge=0)
    symbol: Symbols = Field(Symbols.X, description="Symbol of the new atoms")

    def run(self, vis: "ZnDraw", **kwargs) -> None:
        atoms = vis.atoms
        if len(vis) > vis.step + 1:
            del vis[vis.step + 1 :]

        for atom_id in vis.selection:
            atom = ase.Atom(atoms[atom_id].symbol, atoms[atom_id].position)
            atom.position += np.array([self.x, self.y, self.z])
            atom.symbol = self.symbol.name if self.symbol.name != "X" else atom.symbol
            atoms += atom
            del atoms.arrays["colors"]
            del atoms.arrays["radii"]
            if hasattr(atoms, "connectivity"):
                del atoms.connectivity

        vis.append(atoms)
        vis.selection = []


class ChangeType(UpdateScene):
    symbol: Symbols

    def run(self, vis: "ZnDraw", **kwargs) -> None:
        if len(vis) > vis.step + 1:
            del vis[vis.step + 1 :]

        atoms = vis.atoms
        for atom_id in vis.selection:
            atoms[atom_id].symbol = self.symbol.name

        del atoms.arrays["colors"]
        del atoms.arrays["radii"]
        if hasattr(atoms, "connectivity"):
            # vdW radii might change
            del atoms.connectivity

        vis.append(atoms)
        vis.selection = []


class AddLineParticles(UpdateScene):
    symbol: Symbols
    steps: int = Field(10, le=100, ge=1)

    def run(self, vis: "ZnDraw", **kwargs) -> None:
        if len(vis) > vis.step + 1:
            del vis[vis.step + 1 :]

        atoms = vis.atoms
        for point in vis.points:
            atoms += ase.Atom(self.symbol.name, position=point)

        for _ in range(self.steps):
            vis.append(atoms)


class Wrap(UpdateScene):
    """Wrap the atoms to the cell."""

    recompute_bonds: bool = True
    all: bool = Field(
        False,
        description="Apply to the full trajectory",
    )

    def run(self, vis: "ZnDraw", **kwargs) -> None:
        if self.all:
            for idx, atoms in enumerate(vis):
                atoms.wrap()
                if self.recompute_bonds:
                    delattr(atoms, "connectivity")
                vis[idx] = atoms
        else:
            atoms = vis.atoms
            atoms.wrap()
            if self.recompute_bonds:
                delattr(atoms, "connectivity")
            vis[vis.step] = atoms


class Center(UpdateScene):
    """Move the atoms, such that the selected atom is in the center of the cell."""

    recompute_bonds: bool = True
    dynamic: bool = Field(
        False, description="Move the atoms to the center of the cell at each step"
    )
    wrap: bool = Field(True, description="Wrap the atoms to the cell")
    all: bool = Field(
        False,
        description="Apply to the full trajectory",
    )

    def run(self, vis: "ZnDraw", **kwargs) -> None:
        selection = vis.selection
        if len(selection) < 1:
            vis.log("Please select at least one atom.")
            return

        if not self.dynamic:
            center = vis.atoms[selection].get_center_of_mass()
        else:
            center = None

        if self.all:
            for idx, atoms in enumerate(vis):
                if self.dynamic:
                    center = atoms[selection].get_center_of_mass()
                atoms.positions -= center
                atoms.positions += np.diag(atoms.cell) / 2
                if self.wrap:
                    atoms.wrap()
                if self.recompute_bonds:
                    delattr(atoms, "connectivity")

                vis[idx] = atoms
        else:
            atoms = vis.atoms
            center = atoms[selection].get_center_of_mass()
            atoms.positions -= center
            atoms.positions += np.diag(atoms.cell) / 2
            if self.wrap:
                atoms.wrap()
            if self.recompute_bonds:
                delattr(atoms, "connectivity")

            vis[vis.step] = atoms


class Replicate(UpdateScene):
    x: int = Field(2, ge=1)
    y: int = Field(2, ge=1)
    z: int = Field(2, ge=1)

    keep_box: bool = Field(False, description="Keep the original box size")
    all: bool = Field(
        False,
        description="Apply to the full trajectory",
    )

    def run(self, vis: "ZnDraw", **kwargs) -> None:
        if self.all:
            for idx, atoms in enumerate(vis):
                atoms = atoms.repeat((self.x, self.y, self.z))
                if self.keep_box:
                    atoms.cell = vis[idx].cell
                vis[idx] = atoms
        else:
            atoms = vis.atoms
            atoms = atoms.repeat((self.x, self.y, self.z))
            if self.keep_box:
                atoms.cell = vis.atoms.cell
            vis[vis.step] = atoms


class NewCanvas(UpdateScene):
    """Clear the scene, deleting all atoms and points."""

    def run(self, vis: "ZnDraw", **kwargs) -> None:
        from zndraw.draw import Plane

        del vis[vis.step + 1 :]
        vis.points = []
        vis.append(ase.Atoms())
        vis.selection = []
        step = len(vis) - 1
        vis.step = step
        vis.bookmarks = vis.bookmarks | {step: "New Scene"}
        vis.camera = {"position": [0, 0, -15], "target": [0, 0, 0]}
        vis.geometries = [
            Plane(
                position=[0, 0, 0],
                rotation=[0, 0, 0],
                scale=[1, 1, 1],
                width=10,
                height=10,
            )
        ]


class RemoveAtoms(UpdateScene):
    """Remove the current scene."""

    def run(self, vis: "ZnDraw", **kwargs) -> None:
        del vis[vis.step]


class RunType(UpdateScene, arbitrary_types_allowed=True):
    discriminator: str = Field(..., description="Type of run to perform.")
    max_steps: int = Field(50, ge=1)
    request: str = Field(..., description="Request to send to server.")

    # def get_endpoint(self):
    #     if self.discriminator == "Generate":
    #         return generate
    #     elif self.discriminator == "Hydrogenate":
    #         return hydrogenate
    #     elif self.discriminator == "Relax":
    #         return relax
    #     else:
    #         raise ValueError(f"Unknown run type {self.discriminator}")

    def run(
        self,
        vis: ZnDraw,
        client_address: str,
        calculators: dict,
        timeout: float,
        remove_isolated_atoms: bool = True,
    ) -> ase.Atoms:
        pass
        # vis.log(f"Running {self.discriminator}")
        # logging.debug(f"Reached {self.discriminator} run method")
        # run_settings = format_run_settings(
        #     vis,
        #     run_type=self.discriminator.lower(),
        #     max_steps=self.max_steps,
        #     timeout=timeout,
        # )
        # if run_settings.atoms is None or len(run_settings.atoms) == 0:
        #     vis.log(f"No atoms to {self.discriminator.lower()}")
        #     return
        # logging.debug("Formated run settings; vis.atoms was accessed")
        # generation_calc = calculators.get("generation", None)
        # if generation_calc is None:
        #     vis.log("No loaded generation model, will try posting remote request")
        #     json_request = settings_to_json(run_settings)
        #     response = _post_request(
        #         client_address, json_data_str=json_request, name=self.request
        #     )
        #     modified_atoms = [
        #         atoms_from_json(atoms_json) for atoms_json in response.json()["atoms"]
        #     ]
        # else:
        #     logging.debug(f"Calling {self.discriminator.lower()} function")
        #     modified_atoms, _ = self.get_endpoint()(run_settings, self.calculators)
        # logging.debug(f"{self.discriminator} function returned, adding atoms to vis")
        # if remove_isolated_atoms:
        #     modified_atoms.append(
        #         remove_isolated_atoms_using_covalent_radii(modified_atoms[-1])
        #     )
        # else:
        #     modified_atoms.append(modified_atoms[-1])
        # vis.extend(modified_atoms)
        # vis.log(f"Received back {len(modified_atoms)} atoms.")
        # return modified_atoms[-1]


class Model(UpdateScene, arbitrary_types_allowed=True):
    """
    Click on `run type` to select the type of run to perform.\n
    The usual workflow is to first generate a structure, then hydrogenate it, and finally relax it.
    """

    discriminator: str
    # calculator_dict: dict | None = None
    client_address: str | None = None
    run_type: RunType = Field(discriminator="discriminator")

    # @computed_field
    # @property
    # def calculators(self) -> dict | None:
    #     return self.calculator_dict

    def run(self, vis: ZnDraw, **kwargs) -> None:
        logging.debug("-" * 72)
        vis.log("Sending request to inference server.")
        logging.debug(f"Vis token: {vis.token}")
        logging.debug("Accessing vis and vis.step for the first time")
        if len(vis) > vis.step + 1:
            del vis[vis.step + 1 :]
        if kwargs["calculators"] is None:
            raise ValueError("No calculators provided")
        logging.debug("Accessing vis.bookmarks")
        vis.bookmarks = vis.bookmarks | {
            vis.step: f"Running {self.run_type.discriminator}"
        }
        timeout = kwargs.get("timeout", 60)
        self.run_type.run(
            vis=vis,
            client_address=self.client_address,
            calculators=kwargs["calculators"],
            timeout=timeout,
        )
        logging.debug("-" * 72)


class DiffusionModelling(Model):
    """
    Click on `run type` to select the type of run to perform.\n
    The usual workflow is to first generate a structure, then hydrogenate it, and finally relax it.
    """

    discriminator: str = "DiffusionModelling"
    client_address: str = "http://127.0.0.1:5000/run"


methods = t.Union[
    Delete,
    Rotate,
    Translate,
    Duplicate,
    ChangeType,
    AddLineParticles,
    Wrap,
    Center,
    Replicate,
    Connect,
    NewCanvas,
    RemoveAtoms,
    Model,
]


class Modifier(MethodsCollection):
    """Run modifications on the scene"""

    method: methods = Field(
        ..., description="Modify method", discriminator="discriminator"
    )
