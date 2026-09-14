import os
import sys
import json
import logging
import subprocess
import threading
from typing import Dict, Any, List, Optional, Tuple

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hardware_cache.json")

# Windows-only subprocess flag — defined safely for all platforms
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def is_ec2() -> bool:
    """Returns True if running on an AWS EC2 instance (detected via IMDS)."""
    try:
        import urllib.request
        with urllib.request.urlopen(
            "http://169.254.169.254/latest/meta-data/instance-type", timeout=1
        ) as resp:
            return bool(resp.read())
    except Exception:
        return False


class HardwareDetector:
    """
    Intelligent Hardware Auto-Detection and Dynamic Resource Optimization Engine.
    Discovers CPU cores, RAM, GPU device, VRAM, CUDA compute support,
    and probes FFmpeg hardware video encoders (NVENC, MF, QSV, AMF).
    Automatically prioritizes GPU acceleration across all processing pipelines.
    Fully cross-platform: Windows, Linux, macOS, and AWS EC2.
    """

    _cached_telemetry: Optional[Dict[str, Any]] = None
    _lock = threading.Lock()

    @classmethod
    def load_disk_cache(cls) -> bool:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    cls._cached_telemetry = json.load(f)
                return True
            except Exception:
                pass
        return False

    @classmethod
    def save_disk_cache(cls, data: Dict[str, Any]):
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    @classmethod
    def get_ram_info(cls) -> Tuple[float, float]:
        """Returns (total_gb, available_gb) using psutil (cross-platform) with Windows fallback."""
        # Try psutil first (cross-platform, works on Linux EC2)
        try:
            import psutil
            mem = psutil.virtual_memory()
            total_gb = round(mem.total / (1024 ** 3), 2)
            avail_gb = round(mem.available / (1024 ** 3), 2)
            return total_gb, avail_gb
        except ImportError:
            pass

        # Windows-only ctypes fallback (when psutil is not installed)
        if sys.platform == "win32":
            try:
                import ctypes
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]
                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                    total_gb = round(stat.ullTotalPhys / (1024 ** 3), 2)
                    avail_gb = round(stat.ullAvailPhys / (1024 ** 3), 2)
                    return total_gb, avail_gb
            except Exception:
                pass

        # Final fallback (safe conservative defaults)
        return 8.0, 4.0

    @classmethod
    def get_cpu_info(cls) -> Dict[str, Any]:
        logical_cores = os.cpu_count() or 4
        # Cross-platform CPU name detection
        cpu_name = "Generic Multi-Core CPU"
        if sys.platform == "win32":
            cpu_name = os.environ.get("PROCESSOR_IDENTIFIER", cpu_name)
            if "Intel64" in cpu_name or "AMD64" in cpu_name:
                cpu_name = cpu_name.split(" ")[-1] if " " in cpu_name else cpu_name
        elif sys.platform == "linux":
            try:
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if line.startswith("model name"):
                            cpu_name = line.split(":", 1)[1].strip()
                            break
            except Exception:
                pass

        total_ram_gb, avail_ram_gb = cls.get_ram_info()
        return {
            "name": cpu_name,
            "logical_cores": logical_cores,
            "total_ram_gb": total_ram_gb,
            "available_ram_gb": avail_ram_gb
        }


    @classmethod
    def get_gpu_info(cls) -> Dict[str, Any]:
        has_cuda = False
        gpu_name = "N/A"
        vram_mb = 0
        supported_cuda_types = ["float32"]

        # Fast nvidia-smi check (works on both Windows and Linux EC2)
        try:
            smi_cmd = ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]
            smi_kwargs = {"capture_output": True, "text": True, "timeout": 2}
            if sys.platform == "win32":
                smi_kwargs["creationflags"] = _CREATE_NO_WINDOW
            proc = subprocess.run(smi_cmd, **smi_kwargs)
            if proc.returncode == 0 and proc.stdout.strip():
                line = proc.stdout.strip().split("\n")[0]
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    gpu_name = parts[0]
                    vram_mb = int(parts[1])
                has_cuda = True
        except Exception:
            pass

        if has_cuda:
            try:
                import ctranslate2
                if ctranslate2.get_cuda_device_count() > 0:
                    supported_cuda_types = list(ctranslate2.get_supported_compute_types("cuda"))
            except Exception:
                pass

        vram_gb = round(vram_mb / 1024.0, 2) if vram_mb > 0 else 0.0

        return {
            "has_gpu": (gpu_name != "N/A") or has_cuda,
            "has_cuda": has_cuda,
            "gpu_name": gpu_name,
            "vram_mb": vram_mb,
            "vram_gb": vram_gb,
            "supported_cuda_types": supported_cuda_types
        }

    @classmethod
    def probe_ffmpeg_hardware_encoder(cls) -> Dict[str, Any]:
        candidates = [
            {"codec": "h264_nvenc", "type": "GPU (NVIDIA NVENC)", "args": ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20"]},
            {"codec": "h264_mf", "type": "GPU (Windows MediaFoundation)", "args": ["-c:v", "h264_mf", "-b:v", "6M"]},
            {"codec": "h264_qsv", "type": "GPU (Intel QuickSync)", "args": ["-c:v", "h264_qsv", "-global_quality", "20"]},
            {"codec": "h264_amf", "type": "GPU (AMD AMF)", "args": ["-c:v", "h264_amf", "-quality", "speed"]},
            {"codec": "libx264", "type": "CPU Multi-threaded (libx264)", "args": ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"]}
        ]

        # On Linux/EC2, skip Windows-only encoders (h264_mf = Windows MediaFoundation only)
        if sys.platform != "win32":
            candidates = [c for c in candidates if c["codec"] != "h264_mf"]

        for cand in candidates:
            test_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
                *cand["args"], "-f", "null", "-"
            ]
            try:
                kwargs = {"capture_output": True, "text": True, "timeout": 2}
                if sys.platform == "win32":
                    kwargs["creationflags"] = _CREATE_NO_WINDOW
                res = subprocess.run(test_cmd, **kwargs)
                if res.returncode == 0:
                    return cand
            except Exception:
                continue

        return candidates[-1]

    @classmethod
    def get_hardware_telemetry(cls, force_refresh: bool = False) -> Dict[str, Any]:
        with cls._lock:
            if not force_refresh and cls._cached_telemetry is not None:
                return cls._cached_telemetry
            
            if not force_refresh and cls.load_disk_cache():
                return cls._cached_telemetry

            cpu = cls.get_cpu_info()
            gpu = cls.get_gpu_info()
            encoder = cls.probe_ffmpeg_hardware_encoder()

            cuda_types = gpu.get("supported_cuda_types", [])
            if gpu.get("has_cuda"):
                if "float16" in cuda_types: compute_type = "float16"
                elif "bfloat16" in cuda_types: compute_type = "bfloat16"
                elif "int8_float16" in cuda_types: compute_type = "int8_float16"
                else: compute_type = "float32"
            else:
                compute_type = "int8"

            vram_gb = gpu.get("vram_gb", 0.0)
            if gpu.get("has_cuda"):
                if vram_gb >= 8.0: optimal_whisper = "large-v3"
                elif vram_gb >= 4.0: optimal_whisper = "medium"
                elif vram_gb >= 1.5: optimal_whisper = "small"
                else: optimal_whisper = "base"
            else:
                optimal_whisper = "medium" if (cpu["total_ram_gb"] >= 16.0 and cpu["logical_cores"] >= 8) else "small"

            telemetry = {
                "cpu_name": cpu["name"],
                "logical_cores": cpu["logical_cores"],
                "total_ram_gb": cpu["total_ram_gb"],
                "available_ram_gb": cpu["available_ram_gb"],
                "has_gpu": gpu["has_gpu"],
                "has_cuda": gpu["has_cuda"],
                "gpu_name": gpu["gpu_name"],
                "vram_mb": gpu["vram_mb"],
                "vram_gb": gpu["vram_gb"],
                "supported_cuda_types": cuda_types,
                "compute_type": compute_type,
                "optimal_whisper_model": optimal_whisper,
                "primary_device": "cuda" if gpu["has_cuda"] else "cpu",
                "ffmpeg_encoder": encoder["codec"],
                "ffmpeg_encoder_type": encoder["type"],
                "ffmpeg_encoder_args": encoder["args"]
            }

            cls._cached_telemetry = telemetry
            cls.save_disk_cache(telemetry)
            return telemetry

    @classmethod
    def get_optimal_settings(cls, profile_setting: str = "auto") -> Dict[str, Any]:
        tele = cls.get_hardware_telemetry()
        cores = tele["logical_cores"]
        has_cuda = tele["has_cuda"]
        primary_device = tele["primary_device"]
        compute_type = tele["compute_type"]

        if "Low-End" in profile_setting:
            whisper_model = "small"
            ffmpeg_threads = str(max(1, cores // 2))
            cpu_threads = max(1, cores // 2)
        elif "Balanced" in profile_setting or "Mid-Range" in profile_setting:
            whisper_model = "medium"
            ffmpeg_threads = str(cores)
            cpu_threads = cores
        elif "High-End" in profile_setting:
            whisper_model = "large-v3" if has_cuda and tele["vram_gb"] >= 6.0 else "medium"
            ffmpeg_threads = "0"
            cpu_threads = cores
        else:
            whisper_model = tele["optimal_whisper_model"]
            ffmpeg_threads = "0"
            cpu_threads = cores

        return {
            "whisper_model": whisper_model,
            "compute_type": compute_type,
            "device": primary_device,
            "device_index": 0 if has_cuda else None,
            "cpu_threads": cpu_threads,
            "ffmpeg_threads": ffmpeg_threads,
            "ffmpeg_preset": "ultrafast",
            "ffmpeg_encoder": tele["ffmpeg_encoder"],
            "ffmpeg_encoder_args": tele["ffmpeg_encoder_args"],
            "ffmpeg_encoder_type": tele["ffmpeg_encoder_type"],
            "has_gpu": tele["has_gpu"],
            "has_cuda": has_cuda,
            "gpu_name": tele["gpu_name"],
            "vram_gb": tele["vram_gb"],
            "cpu_name": tele["cpu_name"],
            "total_ram_gb": tele["total_ram_gb"],
            "summary": (
                f"GPU: {tele['gpu_name']} ({tele['vram_gb']}GB | {compute_type}) | "
                f"Enc: {tele['ffmpeg_encoder_type']} | CPU: {cores} Cores"
                if tele["has_gpu"] else
                f"CPU Mode: {cores} Cores ({tele['total_ram_gb']}GB RAM) | Multi-Threaded libx264"
            )
        }
