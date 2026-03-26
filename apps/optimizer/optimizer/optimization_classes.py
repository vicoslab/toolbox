from dataclasses import dataclass, field, asdict
from scipy.optimize import differential_evolution
from shapely.geometry import Polygon, Point, LineString
from svgpathtools import svg2paths
from typing import Optional
import torch
import numpy as np
import json

import shapely
from shapely.plotting import plot_polygon, plot_line
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge, Ellipse, Rectangle
from concurrent.futures import ThreadPoolExecutor, as_completed

@dataclass
class CameraConfig:
    focal_length:       float = 50.0
    sensor_width:       float = 60.0
    pixel_size:         float = 0.007
    f_number:           float = 8.0
    max_pixel_on_obj:   float = 0.5
    optimize_focus:     float = False

    def to_json(self):
        return asdict(self)

    @classmethod
    def from_json(cls, d):
        return cls(**d)


@dataclass
class SceneConfig:
    points:         np.ndarray
    normals:        np.ndarray
    wall_segments:  np.ndarray
    obj_poly:       Polygon
    box_width:      float
    box_height:     float
    cam_conf:       CameraConfig = field(default_factory=CameraConfig)
    num_cameras:    int = 6

    @classmethod
    def from_svg(
        cls,
        svg_file:       str,
        real_width_mm:  float,
        box_width:      float,
        box_height:     float,
        cam_conf:       Optional[CameraConfig] = None,
        num_cameras:    int = 6,
        res_mm:         float = 1.0,
    ):
        paths, _ = svg2paths(svg_file)

        all_bounds = [p.bbox() for p in paths]
        svg_min_x = min(b[0] for b in all_bounds)
        svg_max_x = max(b[1] for b in all_bounds)
        scale = real_width_mm / (svg_max_x - svg_min_x)

        check_res = res_mm * 0.5
        master_points = []

        for path in paths:
            path_len_mm = path.length() * scale
            num_pts = max(10, int(path_len_mm / check_res))

            for i in range(num_pts):
                t = i / (num_pts - 1)
                p_complex = path.point(t)
                tangent = path.unit_tangent(t)
                master_points.append({
                    'pos':                  (p_complex.real * scale, p_complex.imag * scale),
                    'normal':               tangent * 1j,
                    'is_optimizer_target':  (i % 2 == 0),
                })
        
        obj_poly = Polygon([pt['pos'] for pt in master_points])
        centroid = np.array(obj_poly.centroid.coords[0])
        epsilon  = check_res * 0.1

        positions, normals_list = [], []
        for pt in master_points:
            if not pt['is_optimizer_target']: continue
            px, py = pt['pos']
            norm = pt['normal']
            nx, ny = norm.real, norm.imag

            if obj_poly.contains(Point(px + nx * epsilon, py + ny * epsilon)):
                nx, ny = -nx, -ny

            positions.append([px, py])
            normals_list.append([nx, ny])
        
        points = np.array(positions, dtype=np.float32) - centroid
        normals = np.array(normals_list, dtype=np.float32)

        obj_poly_centered = shapely.transform(obj_poly, lambda x: x - centroid)

        wall_coords = np.array(obj_poly_centered.exterior.coords, dtype=np.float32)
        wall_segments = np.stack([wall_coords[:-1], wall_coords[1:]], axis=1)
        
        return cls(
            points        = points,
            normals       = normals,
            wall_segments = wall_segments,
            obj_poly      = obj_poly_centered,
            box_width     = box_width,
            box_height    = box_height,
            cam_conf      = cam_conf or CameraConfig(),
            num_cameras   = num_cameras,
        )

    def to_json(self):
        return {
            'points':           self.points.tolist(),
            'normals':          self.normals.tolist(),
            'wall_segments':    self.wall_segments.tolist(),
            "obj_poly":         list(self.obj_poly.exterior.coords),
            'box_width':        self.box_width,
            'box_height':       self.box_height,
            'num_cameras':      self.num_cameras,
            'cam_conf':         self.cam_conf.to_json(),
        }
    
    @classmethod
    def from_json(cls, d):
        return cls(
            points          = np.array(d["points"], dtype=np.float32),
            normals         = np.array(d["normals"], dtype=np.float32),
            wall_segments   = np.array(d["wall_segments"], dtype=np.float32),
            obj_poly        = Polygon(d['obj_poly']),
            box_width       = d['box_width'],
            box_height      = d['box_height'],
            cam_conf        = CameraConfig.from_json(d['cam_conf']),
            num_cameras     = d['num_cameras'],
        )



class CameraOptimizer:
    def __init__(self, scene: SceneConfig, device: str, seed: int = 42, shared_tensors: dict = None):
        self.scene   = scene
        self.device  = device
        self.conf    = scene.cam_conf
        self.tensors = shared_tensors if shared_tensors is not None \
                       else self._preload_tensors(scene.points, scene.normals, scene.wall_segments)
        Npts = len(scene.points)
        self.coverage_scores = torch.zeros(Npts, dtype=torch.float32, device=device)
        self.placed_cameras: list[np.ndarray] = []
        self.log:            list[dict] = []
        self.seed = seed
    
    # -- Public API ----------------------
    def run_greedy(self):
        for i in range(self.scene.num_cameras):
            self._optimize_one_camera(i)
        return self.get_results()
    
    def run_global_refinement(self):
        if not self.placed_cameras:
            raise RuntimeError('Run greedy placement first.')
        
        Ncams = len(self.placed_cameras)
        n = 4 if self.conf.optimize_focus else 3
        n_params = Ncams * n
        w, h = self.scene.box_width, self.scene.box_height
        lower = np.array([-w/2, -h/2, 0]       + ([self.conf.focus_min_mm] if n == 4 else [])) 
        upper = np.array([ w/2,  h/2, 2*np.pi] + ([self.conf.focus_max_mm] if n == 4 else []))
        lower = np.tile(lower, Ncams)
        upper = np.tile(upper, Ncams)

        greedy_flat = np.array(self.placed_cameras).flatten()
        pop_size = 15 * n_params
        noise = np.random.uniform(-0.12, 0.12, (pop_size, n_params)) * (upper - lower)
        init_pop = np.clip(greedy_flat + noise, lower, upper)
        init_pop[0] = greedy_flat

        result = differential_evolution(
            self._global_objective,
            bounds = list(zip(lower, upper)),
            args = (self.tensors, self.conf, Ncams, self.device),
            init = init_pop,
            popsize = 15,
            maxiter = 300,
            seed = 0,
            workers = 1,
            disp = True,
        )

        self.placed_cameras = list(result.x.reshape(Ncams, n))
        return self.get_results()

    def get_results(self):
        return {
            'cameras': np.array(self.placed_cameras).tolist(),
            'coverage_scores': self.coverage_scores.cpu().numpy().tolist(),
            'log': self.log,
            'pct_covered': (self.coverage_scores > 0).float().mean().item() * 100,
        }

    def get_camera_diagnostics(self):
        if not self.placed_cameras:
            raise RuntimeError("No cameras placed yet.")

        f   = self.conf.focal_length
        f_N = self.conf.f_number
        s   = self.conf.sensor_width
        p   = self.conf.pixel_size
        c   = 2 * p
        H   = (f**2) / (f_N * c)

        fov_angle = 2 * np.arctan(s / (2 * f))
        params_t  = torch.tensor(
            np.array(self.placed_cameras), dtype=torch.float32, device=self.device
        )

        cam_pos   = params_t[:, :2]
        theta     = params_t[:, 2]
        cam_dir   = torch.stack([torch.cos(theta), torch.sin(theta)], dim=1)
        points    = self.tensors['points']
        normals   = self.tensors['normals']
        wall_segs = self.tensors['wall_segments']

        vec        = points.unsqueeze(0) - cam_pos.unsqueeze(1)
        dists      = torch.norm(vec, dim=2)
        dir_to_pts = vec / (dists.unsqueeze(2) + 1e-9)

        cos_half_fov        = torch.cos(torch.tensor(fov_angle / 2, device=self.device))
        dots_fov            = (dir_to_pts * cam_dir.unsqueeze(1)).sum(dim=2)
        dots_inc            = (normals.unsqueeze(0) * (-dir_to_pts)).sum(dim=2)
        mask_fov            = dots_fov >= cos_half_fov
        mask_inc            = dots_inc > 0
        mask_vis            = self._raycast(cam_pos, points, wall_segs, self.device)
        valid_geometry_mask = mask_fov & mask_inc & mask_vis

        diagnostics = []
        for ci, cam in enumerate(self.placed_cameras):
            if self.conf.optimize_focus and len(cam) == 4:
                x, y, th, wd = cam
            else:
                x, y, th = cam
                cam_valid = dists[ci][valid_geometry_mask[ci]]
                wd = d_near = d_far = 0.0
                if cam_valid.numel() >= 2:
                    d_min = torch.quantile(cam_valid, 0.35).item()
                    d_max = torch.quantile(cam_valid, 0.65).item()
                    wd    = (2 * d_min * d_max) / (d_min + d_max + 1e-9)

            denom_far = H - (wd - f)
            d_near    = (H * wd) / (H + wd - f) if wd > 0 else 0.0
            d_far     = (H * wd) / denom_far if denom_far > 0 else float('inf')
            
            diagnostics.append({
                "index":       ci,
                "x":           float(x),
                "y":           float(y),
                "theta":       float(th),
                "theta_deg":   float(np.degrees(th)),
                "fov_angle":   float(fov_angle),
                "fov_half_deg": float(np.degrees(fov_angle / 2)),
                "wd_optimal":  float(wd),
                "d_near":      float(d_near),
                "d_far":       float(d_far),
                "n_points_seen": int(valid_geometry_mask[ci].sum().item()),
            })
        return diagnostics

    def save_state(self, path: str):
        state = {
            'scene': self.scene.to_json(),
            'placed_cameras': np.array(self.placed_cameras).tolist(),
            'coverage_scores': self.coverage_scores.cpu().numpy().tolist(),
            'log': self.log,
        }
        with open(path, 'w') as f:
            json.dump(state, f)
    
    @classmethod
    def load_state(cls, path: str, device: str):
        with open(path) as f:
            state = json.load(f)
        obj = cls(SceneConfig.from_json(state['scene']), device)
        obj.placed_cameras = [np.array(c) for c in state['placed_cameras']]
        obj.coverage_scores = torch.tensor(
            state['coverage_scores'], dtype=torch.float32, device=device,
        )
        obj.log = state['log']
        return obj

    # -- Private -------------------------
    def _optimize_one_camera(self, i: int):
        w, h = self.scene.box_width, self.scene.box_height
        bounds = [(-w/2, w/2), (-h/2, h/2), (0, 2*np.pi)]
        if self.conf.optimize_focus:
            bounds.append((1e-9, max(self.scene.box_width, self.scene.box_height)))

        result = differential_evolution(
            self._single_camera_objective,
            bounds,
            args    = (self.tensors, self.conf, self.coverage_scores, self.device),
            popsize = 20,
            maxiter = 1000,
            seed    = self.seed + i,
            workers = 1,
        )

        params = result.x
        self.placed_cameras.append(params)

        params_t = torch.tensor(params, dtype=torch.float32, device=self.device).unsqueeze(0)
        _, inc_quality = self._get_coverage(params_t, self.tensors, self.conf, self.device)
        self.coverage_scores = torch.maximum(self.coverage_scores, inc_quality[0])

        entry = {
            'camera':       i + 1,
            'params':       params.tolist(),
            'pct_covered':  (self.coverage_scores > 0.0).float().mean().item() * 100,
            'mean_quality': self.coverage_scores[self.coverage_scores > 0.0].mean().item() 
                            if (self.coverage_scores > 0.0).any() else 0.0,
        }
        self.log.append(entry)
        print(f"Camera {i+1}: {entry['pct_covered']:.1f}% | quality {entry['mean_quality']:.3f}")
    

    # -- Objectives ----------------------
    @staticmethod
    def _single_camera_objective(
        params_flat:        np.ndarray,
        tensors:            dict,
        conf:               CameraConfig,
        coverage_scores:    torch.Tensor,
        device:             str,
    ):
        params = torch.as_tensor(params_flat, dtype=torch.float32, device=device).unsqueeze(0)
        _, inc_quality = CameraOptimizer._get_coverage(params, tensors, conf, device)
        gain = (inc_quality[0] - coverage_scores).clamp(min=0).sum().item()
        if gain > 1e-6:
            return -gain
        x, y, theta, *_ = params_flat
        angle_to_centroid = np.arctan2(-y, -x)   
        angle_error = abs(np.arctan2(
            np.sin(theta - angle_to_centroid),
            np.cos(theta - angle_to_centroid)
        ))  
        return 1000.0 + angle_error * 100

    @staticmethod
    def _global_objective(
        params_flat:    np.ndarray,
        tensors:        dict,
        conf:           CameraConfig,
        Ncams:          int,
        device:         str,
    ):
        n = 4 if conf.optimize_focus else 3
        params = torch.tensor(
            params_flat.reshape(Ncams, n), dtype=torch.float32, device=device
        )
        _, inc_quality = CameraOptimizer._get_coverage(params, tensors, conf, device)

        best_per_point = inc_quality.max(dim=0).values
        return -best_per_point.sum().item()
    
    # -- Physics -------------------------
    def _preload_tensors(
            self,
            points:        np.ndarray,
            normals:       np.ndarray,
            wall_segments: np.ndarray,
    ):
        simplified = self.scene.obj_poly.simplify(
            tolerance = max(self.scene.box_width, self.scene.box_height) * 0.005
        )
        wall_coords_simple = np.array(simplified.exterior.coords, dtype=np.float32)
        wall_segs_simple   = np.stack([wall_coords_simple[:-1], wall_coords_simple[1:]], axis=1)
        print(f"Wall segments: {len(wall_segments)} full / {len(wall_segs_simple)} simplified")
        return {
            'points':        torch.tensor(points, dtype=torch.float32, device=self.device),
            'normals':       torch.tensor(normals, dtype=torch.float32, device=self.device),
            'wall_segments': torch.tensor(wall_segments, dtype=torch.float32, device=self.device),
            'wall_segments_fast':  torch.tensor(wall_segs_simple, dtype=torch.float32, device=self.device),
            'cos_half_fov': torch.tensor(
                np.cos(np.arctan(self.conf.sensor_width / (2 * self.conf.focal_length))),
                dtype=torch.float32, device=self.device
            ),
            'H': torch.tensor(
                self.conf.focal_length**2 / (self.conf.f_number * 2 * self.conf.pixel_size),
                dtype=torch.float32, device=self.device
            ),
        }

    @staticmethod
    def _get_coverage(
        all_params:       torch.tensor,
        tensors:          dict,
        conf:             CameraConfig,
        device:           str,
        detailed:         bool = False,
        use_fast_raycast: bool = True,
    ):
        Ncams         = all_params.shape[0]
        points        = tensors['points']
        normals       = tensors['normals']
        wall_segments = tensors['wall_segments_fast'] if use_fast_raycast else tensors['wall_segments']

        f   = conf.focal_length
        f_N = conf.f_number
        s   = conf.sensor_width
        p   = conf.pixel_size
        c   = 2 * p

        cos_half_fov = tensors['cos_half_fov']
        H            = tensors['H']

        cam_pos = all_params[:, :2]
        theta   = all_params[:, 2]
        cam_dir = torch.stack([torch.cos(theta), torch.sin(theta)], dim=1)

        wd_override = all_params[:, 3] if conf.optimize_focus else None

        vec        = points.unsqueeze(0) - cam_pos.unsqueeze(1)
        dists      = torch.norm(vec, dim=2)
        dir_to_pts = vec / (dists.unsqueeze(2) + 1e-9)

        dots_fov     = torch.sum(dir_to_pts * cam_dir.unsqueeze(1), dim=2)  
        mask_fov     = dots_fov >= cos_half_fov  

        dots_inc = torch.sum(normals.unsqueeze(0) * (-dir_to_pts), dim=2)
        mask_inc = dots_inc > 0.0

        mask_vis            = CameraOptimizer._raycast(cam_pos, points, wall_segments, device)
        valid_geometry_mask = mask_fov & mask_inc & mask_vis

        H = (f ** 2) / (f_N * c)
        d_near_all = torch.zeros(Ncams, device=device)
        d_far_all  = torch.full((Ncams,), float('inf'), device=device)

        for ci in range(Ncams):
            if wd_override is not None:
                wd = wd_override[ci].item()
            else:
                cam_valid = dists[ci][valid_geometry_mask[ci]]
                if cam_valid.numel() < 2:
                    continue
                d_min = torch.quantile(cam_valid, 0.35).item()
                d_max = torch.quantile(cam_valid, 0.65).item()
                wd    = (2 * d_min * d_max) / (d_min + d_max + 1e-9)
            denom_far = H - (wd - f)
            d_near_all[ci] = (H * wd) / (H + (wd - f)) if wd > 0 else 0.0
            d_far_all[ci]  = (H * wd) / denom_far if denom_far > 0 else float('inf')

        mask_dof     = (dists >= d_near_all.unsqueeze(1)) & (dists <= d_far_all.unsqueeze(1))
        actual_p_obj = (p * (dists - f)) / f
        mask_res     = actual_p_obj <= conf.max_pixel_on_obj

        seen_mask         = valid_geometry_mask & mask_dof & mask_res
        incidence_quality = dots_inc.clamp(0, 1) * seen_mask.float()
        quality_per_cam   = torch.where(
            seen_mask.any(dim=1),
            incidence_quality.sum(dim=1) / (seen_mask.sum(dim=1).float() + 1e-9),
            torch.zeros(Ncams, device=device)
        )

        if detailed:
            for ci in range(Ncams):
                print(f"  cam{ci}: geom={valid_geometry_mask[ci].sum()} "
                    f"→ dof={( valid_geometry_mask[ci] & mask_dof[ci]).sum()} "
                    f"→ final={seen_mask[ci].sum()}")

        return quality_per_cam, incidence_quality
    
    @staticmethod
    def _raycast(
        cam_positions: torch.Tensor,
        target_points: torch.Tensor,
        wall_segments: torch.Tensor,
        device:        str,
    ):
        O = cam_positions[:, None, None, :]         # (Ncams, 1, 1, 2)
        T = target_points[None, :, None, :]         # (1, Npts, 1, 2)
        P1 = wall_segments[None, None, :, 0, :]     # (1, 1, Nwalls, 2)
        P2 = wall_segments[None, None, :, 1, :]     # (1, 1, Nwalls, 2)
        R = T - O       
        S = P2 - P1

        def cross2d(a, b):
            return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]
        
        denom = cross2d(R, S)
        denom = torch.where(denom.abs() < 1e-9, torch.tensor(1e-9, device=device), denom)
        diff = P1 - O
        t = cross2d(diff, S) / denom    
        u = cross2d(diff, R) / denom

        collision = (u > 1e-9) & (u < 1 - 1e-9) & (t > 1e-9) & (t < 0.999)
        return ~collision.any(dim=2)




def visualize_placement(optimizer: CameraOptimizer, cmap_name='tab10'):
    conf  = optimizer.conf
    scene = optimizer.scene
    diags = optimizer.get_camera_diagnostics()

    params_t = torch.tensor(
        np.array(optimizer.placed_cameras), dtype=torch.float32, device=optimizer.device,
    )
    _, inc_quality = optimizer._get_coverage(params_t, optimizer.tensors, conf, device=optimizer.device)
    best_quality = inc_quality.max(dim=0).values.cpu().numpy()
    assignment = torch.where(
        inc_quality.max(dim=0).values > 0,
        inc_quality.argmax(dim=0),
        torch.tensor(len(diags), device=optimizer.device)
    ).cpu().numpy()

    points = np.array(scene.points)
    cmap   = plt.get_cmap(cmap_name)

    fig, ax = plt.subplots(figsize=(12, 12))

    ax.add_patch(Rectangle(
        (-scene.box_width/2, -scene.box_height/2),
        scene.box_width, scene.box_height,
        color='gray', fill=False, linestyle='--',
    ))
    plot_polygon(scene.obj_poly, ax=ax, add_points=False, color='black', alpha=0.5)
    ax.scatter(points[:, 0], points[:, 1], c=assignment, cmap=cmap_name, 
               vmin=0, vmax=len(diags), s=2, alpha=0.8, zorder=2)
    
    for d in diags:
        color = cmap(d['index'] / len(diags))
        x, y, th = d['x'], d['y'], d['theta']
        d_near = d['d_near']
        d_far = min(d['d_far'], scene.box_width)
        width = max(d_far - d_near, 1.0)

        wedge = Wedge(
            (x, y), d_far,
            d['theta_deg'] - d['fov_half_deg'],
            d['theta_deg'] + d['fov_half_deg'],
            width = width,
            alpha = 0.15,
            color = color,
        )
        ax.add_patch(wedge)

        near_boundary = Wedge(
            (x, y), d_near,
            d["theta_deg"] - d["fov_half_deg"],
            d["theta_deg"] + d["fov_half_deg"],
            width = d_near * 0.005,
            alpha = 0.3,
            color = color,
        )
        ax.add_patch(near_boundary)

        if d_far != float('inf'):
            far_boundary = Wedge(
                (x, y), d_far,
                d["theta_deg"] - d["fov_half_deg"],
                d["theta_deg"] + d["fov_half_deg"],
                width = d_far * 0.005,
                alpha = 0.3,
                color = color,
            )
            ax.add_patch(far_boundary)

        ax.plot(
            [x, x + d_far * np.cos(th)],
            [y, y + d_far * np.sin(th)],
            '--', color=color, alpha=0.4, linewidth=1,
        )

        ax.plot(x, y, 'o', color=color, markersize=20, zorder=5,
                markeredgecolor='white', markeredgewidth=1.5)
        ax.text(x, y, f'C{d['index']+1}', fontsize=8, zorder=6, 
                color='white', ha='center', va='center', fontweight='bold')

        d_far_str = "∞" if d["d_far"] == float('inf') else f"{d['d_far']:.0f}"
        ax.annotate(
            f'wd={d['wd_optimal']:.0f}\nd_near={d['d_near']:.0f}\nd_far={d_far_str}',
            xy=(x, y),
            xytext=(x + 30, y + 30),
            fontsize=7,
            color=color,
            alpha=0.8,
        )

    covered_pct = (best_quality > 0).mean() * 100
    ax.set_aspect('equal')
    ax.set_xlim(-scene.box_width  * 0.6, scene.box_width  * 0.6)
    ax.set_ylim(-scene.box_height * 0.6, scene.box_height * 0.6)
    ax.set_title(f"Camera Placement — {covered_pct:.1f}% covered | {len(diags)} cameras | Average Incidence: {best_quality.mean():.02f}")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    
    return fig


def run_multistart(
    scene:     SceneConfig,
    device:    str,
    n_starts:  int = 5,
    base_seed: int = 0,
):
    runs = []
    for i in range(n_starts):
        optimizer = CameraOptimizer(scene, device, seed = base_seed + i)
        result = optimizer.run_greedy()
        runs.append({
            'seed':        base_seed + i,
            'optimizer':   optimizer,
            'pct_covered': result['pct_covered'],
            'cameras':     result['cameras'],
            'log':         result['log']
        })
        print(f"Run {i+1}/{n_starts} — {result['pct_covered']:.1f}% covered")
    
    runs.sort(key=lambda r: r['pct_covered'], reverse=True)
    return runs


def run_multistart_multithread(
    scene:       SceneConfig,
    device:      str,
    n_starts:    int = 5,
    base_seed:   int = 0,
    max_workers: int = None,
):
    shared_tensors = CameraOptimizer(scene, device)._preload_tensors(
        scene.points, scene.normals, scene.wall_segments
    )

    def single_run(i: int):
        optimizer = CameraOptimizer(scene, device, seed = base_seed + i)
        result = optimizer.run_greedy()
        print(f"  [seed={base_seed + i}] {result['pct_covered']:.1f}% covered")
        return {
            'seed':        base_seed + i,
            'optimizer':   optimizer,
            'pct_covered': result['pct_covered'],
            'cameras':     result['cameras'],
            'log':         result['log'],
        }
    
    n_workers = max_workers or n_starts
    runs = []
    print(f"Running {n_starts} starts across {n_workers} threads...")
    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(single_run, i): i for i in range(n_starts)}
        for future in as_completed(futures):
            runs.append(future.result())

    runs.sort(key=lambda r: r['pct_covered'], reverse=True)
    return runs

    