import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import json
from pathlib import Path
from datetime import datetime

from optimizer.optimization_classes import CameraConfig, SceneConfig, CameraOptimizer, visualize_placement


def run_optimization(output_dir: Path, params: dict, num_runs: int):
    """
    Run greedy camera placement optimization num_runs times and append results to runs.json.
 
    Args:
        output_dir: directory containing object.svg and where results will be saved
        params:     dict of camera and scene parameters (same keys as params.json)
        num_runs:   how many runs to perform
        log:        optional list to append progress messages to (for live status)
    """
    cam_conf = CameraConfig(
        focal_length     = params['focal_length'],
        sensor_width     = params['sensor_width'],
        pixel_size       = params['pixel_size'],
        f_number         = params['f_number'],
        max_pixel_on_obj = params['max_pixel_on_obj'],
        optimize_focus   = params['optimize_focus'],
    )
 
    print("Parsing SVG and building scene...")
    scene = SceneConfig.from_svg(
        svg_file      = str(output_dir / 'object.svg'),
        real_width_mm = params['real_width_mm'],
        box_width     = params['box_width'],
        box_height    = params['box_height'],
        cam_conf      = cam_conf,
        num_cameras   = params['num_cameras'],
        res_mm        = params['resolution_mm'],
    )
 
    runs_file = output_dir / 'runs.json'
    runs      = json.loads(runs_file.read_text()) if runs_file.exists() else []
    run_index = len(runs)
 
    for i in range(num_runs):
        current_run = run_index + i
        print(f"Starting run {current_run + 1}/{run_index + num_runs} (seed={current_run})...")
 
        opt    = CameraOptimizer(scene, device='cpu', seed=current_run)
        result = opt.run_greedy()
 
        print(f"Run {current_run + 1} complete — {result['pct_covered']:.1f}% covered")
 
        fig = visualize_placement(opt)
        plot_filename = f'run_{current_run}.png'
        fig.savefig(output_dir / plot_filename, format='png', dpi=100, bbox_inches='tight')
        plt.close(fig)
 
        runs.append({
            'timestamp':        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'num_cameras':      params['num_cameras'],
            'camera_positions': result['cameras'],
            'pct_covered':      round(result['pct_covered'], 2),
            'avrg_incidence':   result['mean_quality'],
            'plot':             plot_filename,
        })
 
        # write after every run
        runs_file.write_text(json.dumps(runs, indent=2))
 
    print(f"All {num_runs} run(s) complete.")


if __name__ == '__main__':
    import argparse
 
    parser = argparse.ArgumentParser(description='Run camera placement optimization')
    parser.add_argument('--svg_path',         required=True,                 help='Path to the SVG file')
    parser.add_argument('--output_dir',       required=True,                 help='Directory to save results')
    parser.add_argument('--num_runs',         type=int,   default=1,         help='Number of optimization runs')
    parser.add_argument('--num_cameras',      type=int,   default=6,         help='Number of cameras to place')
    parser.add_argument('--focal_length',     type=float, default=50.0,      help='Focal length in mm')
    parser.add_argument('--sensor_width',     type=float, default=60.0,      help='Sensor width in mm')
    parser.add_argument('--f_number',         type=float, default=8.0,       help='Aperture f-number')
    parser.add_argument('--pixel_size',       type=float, default=0.007,     help='Pixel size in mm')
    parser.add_argument('--max_pixel_on_obj', type=float, default=0.5,       help='Max pixel size on object in mm')
    parser.add_argument('--optimize_focus',   action='store_true',           help='Whether to optimize focus distance')
    parser.add_argument('--box_width',        type=float, default=3000.0,    help='Restricted area width in mm')
    parser.add_argument('--box_height',       type=float, default=3000.0,    help='Restricted area height in mm')
    parser.add_argument('--real_width_mm',    type=float, default=1000.0,    help='Real object width in mm')
    parser.add_argument('--resolution_mm',    type=float, default=1.0,       help='Desired resolution in mm')
    args = parser.parse_args()
 
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
 
    params = {
        'focal_length':     args.focal_length,
        'sensor_width':     args.sensor_width,
        'pixel_size':       args.pixel_size,
        'f_number':         args.f_number,
        'max_pixel_on_obj': args.max_pixel_on_obj,
        'optimize_focus':   args.optimize_focus,
        'real_width_mm':    args.real_width_mm,
        'box_width':        args.box_width,
        'box_height':       args.box_height,
        'num_cameras':      args.num_cameras,
        'resolution_mm':    args.resolution_mm,
    }
 
    # for CLI use
    svg_src = Path(args.svg_path)
    svg_dst = output_dir / 'object.svg'
    if svg_src.resolve() != svg_dst.resolve():
        svg_dst.write_text(svg_src.read_text())
 
    run_optimization(output_dir, params, args.num_runs)