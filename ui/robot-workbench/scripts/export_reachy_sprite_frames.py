from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ROBOT_DIR = ROOT / "src" / "assets" / "robot-3d"
URDF_PATH = ROBOT_DIR / "reachy-mini.urdf"
MESH_DIR = ROBOT_DIR / "meshes"
DEFAULT_OUTPUT_DIR = ROOT / ".codex-runtime" / "sprite-export-python"

LEGACY_GLOBAL_ROTATION = trimesh.transformations.concatenate_matrices(
    trimesh.transformations.rotation_matrix(-math.pi / 2, [0, 1, 0]),
    trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]),
)

VIEWER_CAMERA_PRESET = {
    "position": np.array([-0.16, 0.3, 0.62], dtype=float),
    "target": np.array([0.0, 0.14, 0.0], dtype=float),
    "fov": (40, 40),
    "pad": 1.15,
}

POSES = [
    {
        "id": "idle",
        "head_joints": [0, 0, 0, 0, 0, 0, 0],
        "antennas": [0.08, -0.08],
    },
    {
        "id": "listen",
        "head_joints": [0.05, 0.02, -0.015, 0.015, -0.02, 0.012, -0.01],
        "antennas": [0.22, -0.22],
    },
    {
        "id": "think",
        "head_joints": [0.11, 0.018, -0.025, 0.024, -0.012, 0.006, -0.01],
        "antennas": [0.02, -0.16],
    },
    {
        "id": "speak",
        "head_joints": [0, -0.028, 0.016, -0.016, 0.02, -0.012, 0.012],
        "antennas": [0.14, -0.06],
    },
    {
        "id": "sleep",
        "head_joints": [0, -0.08, 0.038, -0.03, 0.03, -0.02, 0.018],
        "antennas": [-0.22, 0.22],
    },
    {
        "id": "drag",
        "head_joints": [0.16, 0.01, -0.01, 0.01, -0.01, 0.005, -0.005],
        "antennas": [0, 0],
    },
]

WINDOWPET_LAYOUT = [
    ("idle", ["idle"]),
    ("walk", ["listen", "idle", "think", "idle"]),
    ("sit", ["sleep"]),
    ("greet", ["speak", "listen", "speak", "idle"]),
    ("jump", ["think"]),
    ("fall", ["sleep", "think", "sleep"]),
    ("drag", ["drag"]),
    ("crawl", ["listen", "think", "speak", "idle", "listen", "think", "speak", "idle"]),
    ("climb", ["think", "listen", "speak", "idle", "think", "listen", "speak", "idle"]),
]


@dataclass
class Visual:
    mesh_path: Path
    origin: np.ndarray
    rgba: np.ndarray


@dataclass
class Joint:
    name: str
    joint_type: str
    parent: str
    child: str
    origin: np.ndarray
    axis: np.ndarray


def parse_xyz(value: str | None) -> list[float]:
    if not value:
        return [0.0, 0.0, 0.0]
    return [float(part) for part in value.split()]


def parse_rpy(value: str | None) -> list[float]:
    if not value:
        return [0.0, 0.0, 0.0]
    return [float(part) for part in value.split()]


def matrix_from_origin(element: ET.Element | None) -> np.ndarray:
    if element is None:
        return np.eye(4)
    xyz = parse_xyz(element.get("xyz"))
    rpy = parse_rpy(element.get("rpy"))
    translate = trimesh.transformations.translation_matrix(xyz)
    rotate = trimesh.transformations.euler_matrix(rpy[0], rpy[1], rpy[2], axes="sxyz")
    return trimesh.transformations.concatenate_matrices(translate, rotate)


def parse_urdf() -> tuple[dict[str, list[Visual]], dict[str, list[Joint]], list[str]]:
    root = ET.parse(URDF_PATH).getroot()
    links: dict[str, list[Visual]] = {}
    joints_by_parent: dict[str, list[Joint]] = {}
    all_children: set[str] = set()

    for link in root.findall("link"):
        visuals: list[Visual] = []
        for visual in link.findall("visual"):
            geometry = visual.find("geometry")
            mesh = geometry.find("mesh") if geometry is not None else None
            if mesh is None or not mesh.get("filename"):
                continue

            material = visual.find("material")
            color = material.find("color") if material is not None else None
            rgba = np.array(parse_xyz(None), dtype=float)
            if color is not None and color.get("rgba"):
                rgba = np.array([float(part) for part in color.get("rgba").split()], dtype=float)
            else:
                rgba = np.array([1.0, 1.0, 1.0, 1.0], dtype=float)

            visuals.append(
                Visual(
                    mesh_path=ROBOT_DIR / mesh.get("filename"),
                    origin=matrix_from_origin(visual.find("origin")),
                    rgba=rgba,
                )
            )
        links[link.get("name")] = visuals

    for joint in root.findall("joint"):
        parent = joint.find("parent")
        child = joint.find("child")
        if parent is None or child is None:
            continue
        parsed = Joint(
            name=joint.get("name"),
            joint_type=joint.get("type", "fixed"),
            parent=parent.get("link"),
            child=child.get("link"),
            origin=matrix_from_origin(joint.find("origin")),
            axis=np.array(parse_xyz(joint.find("axis").get("xyz") if joint.find("axis") is not None else None), dtype=float),
        )
        joints_by_parent.setdefault(parsed.parent, []).append(parsed)
        all_children.add(parsed.child)

    roots = sorted(set(links) - all_children)
    return links, joints_by_parent, roots


def pose_to_joint_map(pose: dict) -> dict[str, float]:
    head = pose["head_joints"]
    antennas = pose["antennas"]
    return {
        "yaw_body": head[0],
        "stewart_1": head[1],
        "stewart_2": head[2],
        "stewart_3": head[3],
        "stewart_4": head[4],
        "stewart_5": head[5],
        "stewart_6": head[6],
        "left_antenna": -antennas[1],
        "right_antenna": -antennas[0],
    }


def load_mesh(mesh_path: Path, color: np.ndarray, cache: dict[Path, trimesh.Trimesh]) -> trimesh.Trimesh:
    if mesh_path not in cache:
        cache[mesh_path] = trimesh.load(mesh_path, force="mesh")
    mesh = cache[mesh_path].copy()
    rgba = np.clip((color * 255).round(), 0, 255).astype(np.uint8)
    mesh.visual.face_colors = rgba
    return mesh


def get_global_rotation(mode: str) -> np.ndarray:
    if mode == "identity":
        return np.eye(4)
    return LEGACY_GLOBAL_ROTATION


def build_scene(
    links: dict[str, list[Visual]],
    joints_by_parent: dict[str, list[Joint]],
    roots: list[str],
    joint_values: dict[str, float],
    global_rotation: np.ndarray,
) -> trimesh.Scene:
    scene = trimesh.Scene()
    mesh_cache: dict[Path, trimesh.Trimesh] = {}

    def walk(link_name: str, parent_transform: np.ndarray) -> None:
        visuals = links.get(link_name, [])
        for index, visual in enumerate(visuals):
            mesh = load_mesh(visual.mesh_path, visual.rgba, mesh_cache)
            transform = trimesh.transformations.concatenate_matrices(
                global_rotation,
                parent_transform,
                visual.origin,
            )
            scene.add_geometry(mesh, geom_name=f"{link_name}-{index}", transform=transform)

        for joint in joints_by_parent.get(link_name, []):
            joint_transform = joint.origin.copy()
            if joint.joint_type in {"revolute", "continuous"}:
                angle = joint_values.get(joint.name, 0.0)
                if np.linalg.norm(joint.axis) > 0:
                    axis = joint.axis / np.linalg.norm(joint.axis)
                    rotation = trimesh.transformations.rotation_matrix(angle, axis)
                    joint_transform = trimesh.transformations.concatenate_matrices(joint_transform, rotation)

            child_transform = trimesh.transformations.concatenate_matrices(parent_transform, joint_transform)
            walk(joint.child, child_transform)

    for root in roots:
        walk(root, np.eye(4))

    return scene


def get_bounds_corners(bounds: np.ndarray) -> np.ndarray:
    lower = bounds[0]
    upper = bounds[1]
    return np.array(
        [
            [lower[0], lower[1], lower[2]],
            [lower[0], lower[1], upper[2]],
            [lower[0], upper[1], lower[2]],
            [lower[0], upper[1], upper[2]],
            [upper[0], lower[1], lower[2]],
            [upper[0], lower[1], upper[2]],
            [upper[0], upper[1], lower[2]],
            [upper[0], upper[1], upper[2]],
        ],
        dtype=float,
    )


def camera_pose_from_position_target(
    position: np.ndarray,
    target: np.ndarray,
    up: np.ndarray | None = None,
) -> np.ndarray:
    if up is None:
        up = np.array([0.0, 1.0, 0.0], dtype=float)

    z_axis = position - target
    z_axis = z_axis / np.linalg.norm(z_axis)

    x_axis = np.cross(up, z_axis)
    if np.linalg.norm(x_axis) < 1e-8:
        up = np.array([0.0, 0.0, 1.0], dtype=float)
        x_axis = np.cross(up, z_axis)
    x_axis = x_axis / np.linalg.norm(x_axis)

    y_axis = np.cross(z_axis, x_axis)
    y_axis = y_axis / np.linalg.norm(y_axis)

    pose = np.eye(4)
    pose[:3, 0] = x_axis
    pose[:3, 1] = y_axis
    pose[:3, 2] = z_axis
    pose[:3, 3] = position
    return pose


def render_scene(scene: trimesh.Scene, resolution: tuple[int, int], camera_mode: str) -> bytes:
    bounds = scene.bounds
    scene.camera.resolution = resolution

    if camera_mode == "viewer_front":
        pose = camera_pose_from_position_target(
            VIEWER_CAMERA_PRESET["position"],
            VIEWER_CAMERA_PRESET["target"],
        )
        scene.camera.fov = VIEWER_CAMERA_PRESET["fov"]
        scene.camera_transform = trimesh.scene.cameras.look_at(
            points=get_bounds_corners(bounds),
            fov=np.array(VIEWER_CAMERA_PRESET["fov"], dtype=float),
            rotation=pose,
            center=VIEWER_CAMERA_PRESET["target"],
            pad=VIEWER_CAMERA_PRESET["pad"],
        )
    else:
        center = scene.centroid
        diagonal = np.linalg.norm(bounds[1] - bounds[0])
        distance = max(diagonal * 2.4, 0.85)
        scene.set_camera(
            angles=(math.radians(75), 0.0, math.radians(18)),
            distance=distance,
            center=center + np.array([0.0, 0.08, 0.02]),
            resolution=resolution,
            fov=(40, 40),
        )

    return scene.save_image(resolution=resolution, visible=True, background=[255, 255, 255, 0])


def trim_image(png_bytes: bytes) -> Image.Image:
    image = Image.open(trimesh.util.wrap_as_stream(png_bytes)).convert("RGBA")
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return image
    return image.crop(bbox)


def place_on_canvas(
    image: Image.Image,
    canvas_size: tuple[int, int],
    padding: int,
    anchor: str,
) -> tuple[Image.Image, dict]:
    canvas_width, canvas_height = canvas_size
    available_width = max(canvas_width - (padding * 2), 1)
    available_height = max(canvas_height - (padding * 2), 1)

    scale = min(1.0, available_width / image.width, available_height / image.height)
    if scale < 1.0:
        resized_width = max(1, int(round(image.width * scale)))
        resized_height = max(1, int(round(image.height * scale)))
        image = image.resize((resized_width, resized_height), Image.LANCZOS)

    x = (canvas_width - image.width) // 2
    if anchor == "bottom":
        y = canvas_height - padding - image.height
    elif anchor == "top":
        y = padding
    else:
        y = (canvas_height - image.height) // 2

    canvas = Image.new("RGBA", canvas_size, (255, 255, 255, 0))
    canvas.alpha_composite(image, (x, y))
    return canvas, {
        "contentSize": [image.width, image.height],
        "offset": [x, y],
    }


def build_preview_sheet(frames: list[dict], output_dir: Path, frame_size: tuple[int, int], columns: int = 3, gap: int = 28) -> None:
    rows = max(1, math.ceil(len(frames) / columns))
    frame_width, frame_height = frame_size
    sheet_width = columns * frame_width
    sheet_height = rows * frame_height
    sprite_sheet = Image.new("RGBA", (sheet_width, sheet_height), (255, 255, 255, 0))

    preview_width = (columns * frame_width) + ((columns + 1) * gap)
    preview_height = (rows * frame_height) + ((rows + 1) * gap)
    preview_sheet = Image.new("RGBA", (preview_width, preview_height), (245, 242, 237, 255))

    sprite_manifest = {
        "frameSize": [frame_width, frame_height],
        "columns": columns,
        "rows": rows,
        "frames": [],
    }

    for index, frame in enumerate(frames):
        column = index % columns
        row = index // columns
        sprite_x = column * frame_width
        sprite_y = row * frame_height
        preview_x = gap + (column * (frame_width + gap))
        preview_y = gap + (row * (frame_height + gap))

        sprite_sheet.alpha_composite(frame["image"], (sprite_x, sprite_y))
        preview_sheet.alpha_composite(frame["image"], (preview_x, preview_y))
        sprite_manifest["frames"].append(
            {
                "id": frame["id"],
                "fileName": frame["fileName"],
                "frame": [sprite_x, sprite_y, frame_width, frame_height],
                "contentSize": frame["contentSize"],
                "offset": frame["offset"],
            }
        )

    sprite_sheet_path = output_dir / f"spritesheet-{columns}x{rows}.png"
    preview_sheet_path = output_dir / "contact-sheet.png"
    sprite_manifest_path = output_dir / f"spritesheet-{columns}x{rows}.json"

    sprite_sheet.save(sprite_sheet_path)
    preview_sheet.save(preview_sheet_path)
    sprite_manifest_path.write_text(json.dumps(sprite_manifest, indent=2), encoding="utf-8")

    print(f"spritesheet {sprite_sheet_path}")
    print(f"preview {preview_sheet_path}")
    print(f"spritesheet manifest {sprite_manifest_path}")


def write_framed_outputs(output_dir: Path, framed_images: list[dict], canvas_size: tuple[int, int]) -> dict:
    manifest = {"frames": []}

    for frame in framed_images:
        file_path = output_dir / frame["fileName"]
        frame["image"].save(file_path)
        manifest["frames"].append(
            {
                "id": frame["id"],
                "fileName": frame["fileName"],
                "size": list(canvas_size),
                "contentSize": frame["contentSize"],
                "offset": frame["offset"],
            }
        )
        print(f"exported {file_path}")

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"manifest {manifest_path}")
    build_preview_sheet(framed_images, output_dir, canvas_size)
    return manifest


def build_windowpet_pack(
    source_dir: Path,
    output_dir: Path,
    pet_name: str,
    frame_size: int,
    rotation_degrees: int = 0,
    anchor: str = "bottom",
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_images: dict[str, Image.Image] = {}
    for pose in POSES:
        source_path = source_dir / f"{pose['id']}.png"
        if not source_path.exists():
            raise FileNotFoundError(f"Missing source frame: {source_path}")
        source_image = Image.open(source_path).convert("RGBA")
        if rotation_degrees:
            source_image = source_image.rotate(rotation_degrees, expand=True, resample=Image.BICUBIC)
        source_images[pose["id"]] = source_image

    highest_frame_max = max(len(frame_ids) for _, frame_ids in WINDOWPET_LAYOUT)
    total_sprite_line = len(WINDOWPET_LAYOUT)
    sheet = Image.new(
        "RGBA",
        (highest_frame_max * frame_size, total_sprite_line * frame_size),
        (255, 255, 255, 0),
    )

    states_by_line: dict[str, dict] = {}
    states_by_index: dict[str, dict] = {}
    layout_manifest = []

    for row_index, (state_name, frame_ids) in enumerate(WINDOWPET_LAYOUT):
        start_index = (row_index * highest_frame_max) + 1
        end_index = start_index + len(frame_ids) - 1
        states_by_line[state_name] = {
            "spriteLine": row_index + 1,
            "frameMax": len(frame_ids),
        }
        states_by_index[state_name] = {
            "start": start_index,
            "end": end_index,
        }
        layout_manifest.append(
            {
                "state": state_name,
                "frames": frame_ids,
                "start": start_index,
                "end": end_index,
            }
        )

        for column_index, frame_id in enumerate(frame_ids):
            tile, _ = place_on_canvas(
                source_images[frame_id],
                (frame_size, frame_size),
                padding=max(6, frame_size // 12),
                anchor=anchor,
            )
            x = column_index * frame_size
            y = row_index * frame_size
            sheet.alpha_composite(tile, (x, y))

    image_file_name = f"{pet_name}.png"
    sprite_path = output_dir / image_file_name
    sheet.save(sprite_path)

    windowpet_config = {
        "name": pet_name,
        "imageSrc": image_file_name,
        "frameSize": frame_size,
        "highestFrameMax": highest_frame_max,
        "totalSpriteLine": total_sprite_line,
        "states": states_by_line,
    }
    custom_pet_config = {
        "name": pet_name,
        "imageSrc": str(sprite_path),
        "frameSize": frame_size,
        "states": states_by_index,
    }
    layout_path = output_dir / f"{pet_name}.layout.json"
    windowpet_config_path = output_dir / f"{pet_name}.windowpet.json"
    custom_pet_config_path = output_dir / f"{pet_name}.custom.json"

    layout_path.write_text(json.dumps(layout_manifest, indent=2), encoding="utf-8")
    windowpet_config_path.write_text(json.dumps(windowpet_config, indent=2), encoding="utf-8")
    custom_pet_config_path.write_text(json.dumps(custom_pet_config, indent=2), encoding="utf-8")

    print(f"windowpet spritesheet {sprite_path}")
    print(f"windowpet config {windowpet_config_path}")
    print(f"custom config {custom_pet_config_path}")
    print(f"layout manifest {layout_path}")

    return {
        "spriteSheet": str(sprite_path),
        "windowpetConfig": str(windowpet_config_path),
        "customConfig": str(custom_pet_config_path),
        "layout": str(layout_path),
    }


def export_frames(
    output_dir: Path,
    resolution: tuple[int, int],
    canvas_size: tuple[int, int],
    padding: int,
    anchor: str,
    global_rotation_mode: str,
    camera_mode: str,
    pose_id: str | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    links, joints_by_parent, roots = parse_urdf()
    rendered_frames: list[dict] = []
    global_rotation = get_global_rotation(global_rotation_mode)
    poses = [pose for pose in POSES if pose["id"] == pose_id] if pose_id else POSES

    for pose in poses:
        joint_values = pose_to_joint_map(pose)
        scene = build_scene(links, joints_by_parent, roots, joint_values, global_rotation)
        trimmed_image = trim_image(render_scene(scene, resolution, camera_mode))
        image, placement = place_on_canvas(trimmed_image, canvas_size, padding, anchor)
        rendered_frames.append(
            {
                "id": pose["id"],
                "fileName": f"{pose['id']}.png",
                "image": image,
                "contentSize": placement["contentSize"],
                "offset": placement["offset"],
            }
        )

    return write_framed_outputs(output_dir, rendered_frames, canvas_size)


def reframe_existing_frames(
    source_dir: Path,
    output_dir: Path,
    canvas_size: tuple[int, int],
    padding: int,
    anchor: str,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    framed_images: list[dict] = []

    for pose in POSES:
        source_path = source_dir / f"{pose['id']}.png"
        if not source_path.exists():
            raise FileNotFoundError(f"Missing source frame: {source_path}")

        source_image = Image.open(source_path).convert("RGBA")
        image, placement = place_on_canvas(source_image, canvas_size, padding, anchor)
        framed_images.append(
            {
                "id": pose["id"],
                "fileName": source_path.name,
                "image": image,
                "contentSize": placement["contentSize"],
                "offset": placement["offset"],
            }
        )

    return write_framed_outputs(output_dir, framed_images, canvas_size)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Reachy sprite frames from the local URDF")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR / "latest")
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--windowpet-source-dir", type=Path)
    parser.add_argument("--pet-name", default="ReachyMini")
    parser.add_argument("--rotation-degrees", type=int, default=0)
    parser.add_argument("--windowpet-anchor", choices=["center", "bottom", "top"], default="bottom")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--frame-width", type=int, default=512)
    parser.add_argument("--frame-height", type=int, default=512)
    parser.add_argument("--padding", type=int, default=24)
    parser.add_argument("--anchor", choices=["center", "bottom", "top"], default="center")
    parser.add_argument("--global-rotation-mode", choices=["legacy", "identity"], default="legacy")
    parser.add_argument("--camera-mode", choices=["legacy", "viewer_front"], default="legacy")
    parser.add_argument("--pose-id", choices=[pose["id"] for pose in POSES])
    args = parser.parse_args()

    if args.windowpet_source_dir:
        build_windowpet_pack(
            args.windowpet_source_dir,
            args.output_dir,
            args.pet_name,
            args.frame_width,
            args.rotation_degrees,
            args.windowpet_anchor,
        )
        return

    if args.source_dir:
        reframe_existing_frames(
            args.source_dir,
            args.output_dir,
            (args.frame_width, args.frame_height),
            args.padding,
            args.anchor,
        )
        return

    export_frames(
        args.output_dir,
        (args.width, args.height),
        (args.frame_width, args.frame_height),
        args.padding,
        args.anchor,
        args.global_rotation_mode,
        args.camera_mode,
        args.pose_id,
    )


if __name__ == "__main__":
    main()
