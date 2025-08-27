import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import cv2
import numpy as np
from PIL import Image, ImageTk
import threading
import subprocess as sp
import json
import time
import sys
import os

def get_executable_path(name):
    """
    Gets the path to an executable, handling both normal script execution
    and the PyInstaller bundled environment.
    """
    if getattr(sys, 'frozen', False):
        # The application is frozen (packaged by PyInstaller)
        return os.path.join(sys._MEIPASS, name)
    else:
        # The application is running in a normal Python environment
        return name # Assumes ffmpeg/ffprobe are in PATH for development

def parse_frame_rate(rate_str):
    """Safely parse a frame rate string (e.g., '30/1' or '29.97') into a float."""
    try:
        if '/' in rate_str:
            num, den = map(float, rate_str.split('/'))
            if den == 0: return 30.0
            return num / den
        return float(rate_str)
    except (ValueError, TypeError):
        return 30.0

def format_time(seconds):
    """Formats seconds into MM:SS string."""
    if seconds is None or seconds < 0: return "00:00"
    minutes, sec = divmod(int(seconds), 60)
    return f"{minutes:02d}:{sec:02d}"

class VideoComparer(tk.Tk):
    """
    A GUI application for comparing two videos side-by-side with a slider,
    using a system-installed FFmpeg for robust video decoding.
    """
    def __init__(self):
        super().__init__()
        self.title("Side-by-Side Video Comparison Tool (FFmpeg Edition)")
        self.geometry("1280x850")
        self.configure(bg="#2c3e50")

        # --- UPDATED: Get paths to FFmpeg executables ---
        self.ffmpeg_path = get_executable_path('ffmpeg.exe')
        self.ffprobe_path = get_executable_path('ffprobe.exe')

        self.video_path1, self.video_path2 = None, None
        self.video_info1, self.video_info2 = {}, {}
        self.video_name1, self.video_name2 = "", ""
        self.ffmpeg_process1, self.ffmpeg_process2 = None, None
        self.is_playing = False
        self.current_frame_num, self.total_frames = 0, 0
        
        self.fps1, self.fps2 = 30.0, 30.0
        self.video_fps = 30.0
        self.video2_offset = 0

        self.playback_lock = threading.Lock()
        self.display_width, self.display_height = 1200, 675
        self.split_pos = self.display_width // 2
        
        self.duration_known = True
        self.is_fullscreen = False
        self._resize_job = None

        self._setup_styles()
        self._create_widgets()
        self._bind_keys()

        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.after(100, self.check_ffmpeg_installed)

    def check_ffmpeg_installed(self):
        """Checks if ffmpeg and ffprobe are accessible."""
        try:
            sp.run([self.ffmpeg_path, '-version'], check=True, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
            sp.run([self.ffprobe_path, '-version'], check=True, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        except (FileNotFoundError, sp.CalledProcessError):
            messagebox.showerror("FFmpeg Not Found", "FFmpeg could not be found.\nPlease ensure 'ffmpeg.exe' and 'ffprobe.exe' are in the same folder as the application, or that FFmpeg is in your system's PATH.")
            self.destroy()

    def _setup_styles(self):
        """Configure styles for ttk widgets."""
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TButton', background='#3498db', foreground='white', padding=10, relief='flat', font=('Helvetica', 10, 'bold'))
        style.map('TButton', background=[('active', '#2980b9')])
        style.configure('Horizontal.TScale', background='#2c3e50', troughcolor='#34495e', sliderrelief='flat', sliderthickness=20)
        style.configure('TLabel', background='#2c3e50', foreground='white', font=('Helvetica', 11))
        style.configure('Status.TLabel', background='#2c3e50', foreground='#bdc3c7', font=('Helvetica', 9))
        style.configure('TRadiobutton', background='#2c3e50', foreground='white', font=('Helvetica', 9))
        style.map('TRadiobutton', foreground=[('active', 'white')], background=[('active', '#34495e')])


    def _create_widgets(self):
        """Create and arrange all the UI widgets."""
        self.main_frame = tk.Frame(self, bg="#2c3e50")
        self.main_frame.pack(padx=20, pady=20, fill=tk.BOTH, expand=True)
        self.main_frame.rowconfigure(0, weight=1)
        self.main_frame.columnconfigure(0, weight=1)

        self.video_label = tk.Label(self.main_frame, bg="#1c1c1c", text="Load videos to begin comparison", fg="white", font=("Helvetica", 14))
        self.video_label.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        self.video_label.bind('<B1-Motion>', self._on_video_drag)
        self.video_label.bind('<Button-1>', self._on_video_click)
        
        self.controls_frame = tk.Frame(self.main_frame, bg="#2c3e50")
        self.controls_frame.grid(row=1, column=0, sticky="ew")

        mode_frame = tk.Frame(self.controls_frame, bg="#2c3e50")
        mode_frame.pack(fill=tk.X, pady=(0, 10))
        mode_label = ttk.Label(mode_frame, text="Mode (Ctrl+1/2/3/4):")
        mode_label.pack(side=tk.LEFT, padx=(5, 10))
        
        self.comparison_mode_var = tk.StringVar(value="side_by_side")
        modes = [("Side-by-Side", "side_by_side"), ("Overlay", "overlay"), ("Difference", "difference"), ("Toggle", "toggle")]
        for text, mode in modes:
            rb = ttk.Radiobutton(mode_frame, text=text, variable=self.comparison_mode_var, value=mode, command=self._on_comparison_mode_change)
            rb.pack(side=tk.LEFT, padx=5)

        self.seek_bar = ttk.Scale(self.controls_frame, from_=0, to=100, orient="horizontal", style='Horizontal.TScale', command=self.seek_video)
        self.seek_bar.pack(fill=tk.X, pady=(5, 15), padx=5)

        button_bar = tk.Frame(self.controls_frame, bg="#2c3e50")
        button_bar.pack()

        self.load_btn1 = ttk.Button(button_bar, text="Load Video 1", command=lambda: self.load_video(1))
        self.load_btn1.pack(side=tk.LEFT, padx=10)
        self.prev_frame_btn = ttk.Button(button_bar, text="<<", command=lambda: self.step_frame(-1), state=tk.DISABLED, width=4)
        self.prev_frame_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.play_pause_btn = ttk.Button(button_bar, text="▶ Play", command=self.toggle_play_pause, state=tk.DISABLED)
        self.play_pause_btn.pack(side=tk.LEFT, padx=5)
        self.next_frame_btn = ttk.Button(button_bar, text=">>", command=lambda: self.step_frame(1), state=tk.DISABLED, width=4)
        self.next_frame_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.load_btn2 = ttk.Button(button_bar, text="Load Video 2", command=lambda: self.load_video(2))
        self.load_btn2.pack(side=tk.LEFT, padx=10)
        
        self.snapshot_btn = ttk.Button(button_bar, text="📷 Snapshot", command=self.save_snapshot, state=tk.DISABLED)
        self.snapshot_btn.pack(side=tk.LEFT, padx=10)

        offset_label = ttk.Label(button_bar, text="Video 2 Offset:")
        offset_label.pack(side=tk.LEFT, padx=(20, 5))
        offset_minus_btn = ttk.Button(button_bar, text="-", command=lambda: self._adjust_offset(-1), width=2)
        offset_minus_btn.pack(side=tk.LEFT)
        self.offset_var = tk.StringVar(value="0")
        self.offset_var.trace_add("write", self._validate_offset_input)
        offset_entry = ttk.Entry(button_bar, textvariable=self.offset_var, width=5, justify='center')
        offset_entry.pack(side=tk.LEFT, padx=2)
        offset_plus_btn = ttk.Button(button_bar, text="+", command=lambda: self._adjust_offset(1), width=2)
        offset_plus_btn.pack(side=tk.LEFT)
        
        self.fullscreen_btn = ttk.Button(button_bar, text="Full Screen", command=self.toggle_fullscreen)
        self.fullscreen_btn.pack(side=tk.RIGHT, padx=20)

        self.status_var = tk.StringVar(value="Ready. Please load two videos.")
        self.status_bar = ttk.Label(self.main_frame, textvariable=self.status_var, style='Status.TLabel', anchor='w')
        self.status_bar.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        self.update_idletasks()

    def _bind_keys(self):
        """Bind keyboard shortcuts."""
        self.bind('<space>', lambda event: self.toggle_play_pause())
        self.bind('<Right>', lambda event: self.step_frame(1))
        self.bind('<Left>', lambda event: self.step_frame(-1))
        self.bind('<Home>', lambda event: self.seek_to_start())
        self.bind('<End>', lambda event: self.seek_to_end())
        self.bind('<Control-s>', lambda event: self.save_snapshot())
        self.bind('<Control-1>', lambda e: self.comparison_mode_var.set("side_by_side") or self._on_comparison_mode_change())
        self.bind('<Control-2>', lambda e: self.comparison_mode_var.set("overlay") or self._on_comparison_mode_change())
        self.bind('<Control-3>', lambda e: self.comparison_mode_var.set("difference") or self._on_comparison_mode_change())
        self.bind('<Control-4>', lambda e: self.comparison_mode_var.set("toggle") or self._on_comparison_mode_change())
        self.bind('<F11>', self.toggle_fullscreen)
        self.bind('<Escape>', self.exit_fullscreen)
        self.bind('<Configure>', self._on_window_resize)


    def load_video(self, video_num):
        """Open a file dialog and get video info using ffprobe."""
        self.update_status_bar(f"Opening file dialog for Video {video_num}...")
        file_path = filedialog.askopenfilename(parent=self, title=f"Select Video {video_num}", filetypes=(("All Video Files", "*.mp4;*.mkv;*.avi;*.mov;*.webm"), ("All files", "*.*")))
        if not file_path: 
            self.update_status_bar("File selection cancelled.")
            return

        self.update_status_bar(f"Analyzing video: {file_path.split('/')[-1]}...")
        filename = file_path.split('/')[-1]

        try:
            info_command = [self.ffprobe_path, '-v', 'quiet', '-print_format', 'json', '-show_streams', file_path]
            result = sp.run(info_command, capture_output=True, text=True, check=True)
            all_streams = json.loads(result.stdout)['streams']
            
            video_stream = next((s for s in all_streams if s.get('codec_type') == 'video'), None)
            
            if video_stream is None:
                messagebox.showerror("Stream Error", f"No video stream found in:\n{file_path}")
                return
            
            if 'width' not in video_stream or 'height' not in video_stream:
                 messagebox.showerror("Stream Error", f"Could not determine dimensions for:\n{file_path}")
                 return
            video_stream['nb_read_frames'] = '0'

        except (sp.CalledProcessError, FileNotFoundError, json.JSONDecodeError, IndexError) as e:
            messagebox.showerror("FFprobe Error", f"Could not get video info from:\n{file_path}\n\nError: {e}")
            return

        if video_num == 1:
            self.video_path1, self.video_info1, self.video_name1 = file_path, video_stream, filename
            self.update_status_bar("Video 1 loaded. Please load Video 2.")
        else:
            self.video_path2, self.video_info2, self.video_name2 = file_path, video_stream, filename
            self.update_status_bar("Video 2 loaded. Ready to play.")

        if self.video_path1 and self.video_path2:
            self.initialize_playback()

    def initialize_playback(self):
        """Sets up video properties once both videos are loaded."""
        try:
            self.fps1 = parse_frame_rate(self.video_info1.get('avg_frame_rate', '30/1'))
            self.fps2 = parse_frame_rate(self.video_info2.get('avg_frame_rate', '30/1'))
            self.video_fps = (self.fps1 + self.fps2) / 2.0

            fc1 = int(self.fps1 * float(self.video_info1.get('duration', 0)))
            fc2 = int(self.fps2 * float(self.video_info2.get('duration', 0)))
            self.total_frames = min(fc1, fc2) if fc1 > 0 and fc2 > 0 else 0
            
            self.handle_resize()

        except Exception as e:
            messagebox.showerror("Initialization Error", f"Could not parse video metadata.\nError: {e}")
            return
        
        if self.total_frames <= 0:
            self.duration_known = False
            self.total_frames = float('inf')
            self.seek_bar.config(state=tk.DISABLED)
        else:
            self.duration_known = True
            self.seek_bar.config(to=self.total_frames - 1, state=tk.NORMAL)

        self.current_frame_num = 0
        if self.duration_known: self.seek_bar.set(0)
        self.play_pause_btn.config(state=tk.NORMAL)
        self.prev_frame_btn.config(state=tk.NORMAL)
        self.next_frame_btn.config(state=tk.NORMAL)
        self.snapshot_btn.config(state=tk.NORMAL)
        self.video2_offset = 0
        self.offset_var.set("0")
        self.update_status_bar("Ready")
        self.display_single_frame(0)

    def calculate_display_size(self, w, h, tw, th):
        ar = w / h
        return (int(th * ar), th) if tw / th > ar else (tw, int(tw / ar))

    def start_ffmpeg_processes(self, frame_number):
        self.stop_ffmpeg_processes()
        time_offset1 = frame_number / self.fps1
        time_offset2 = (frame_number + self.video2_offset) / self.fps2

        vf_filter = f"scale={self.display_width}:{self.display_height}"
        common_args = ['-vf', vf_filter, '-f', 'image2pipe', '-vcodec', 'rawvideo', '-pix_fmt', 'bgr24', '-']
        
        command1 = [self.ffmpeg_path, '-ss', str(time_offset1), '-i', self.video_path1, *common_args]
        command2 = [self.ffmpeg_path, '-ss', str(time_offset2), '-i', self.video_path2, *common_args]
        try:
            creation_flags = sp.CREATE_NO_WINDOW if hasattr(sp, 'CREATE_NO_WINDOW') else 0
            self.ffmpeg_process1 = sp.Popen(command1, stdout=sp.PIPE, stderr=sp.DEVNULL, creationflags=creation_flags)
            self.ffmpeg_process2 = sp.Popen(command2, stdout=sp.PIPE, stderr=sp.DEVNULL, creationflags=creation_flags)
        except Exception as e:
            messagebox.showerror("FFmpeg Error", f"Failed to start FFmpeg processes: {e}")
            with self.playback_lock: self.is_playing = False

    def stop_ffmpeg_processes(self):
        for proc in [self.ffmpeg_process1, self.ffmpeg_process2]:
            if proc and proc.poll() is None: 
                try:
                    proc.kill()
                except Exception as e:
                    print(f"Error killing FFmpeg process: {e}")
        self.ffmpeg_process1, self.ffmpeg_process2 = None, None

    def toggle_play_pause(self):
        if self.play_pause_btn['state'] == tk.DISABLED: return
        with self.playback_lock: self.is_playing = not self.is_playing
        if self.is_playing:
            self.play_pause_btn.config(text="❚❚ Pause")
            self.update_status_bar("Playing")
            self.start_ffmpeg_processes(self.current_frame_num)
            threading.Thread(target=self.video_playback_loop, daemon=True).start()
        else:
            self.play_pause_btn.config(text="▶ Play")
            self.update_status_bar("Paused")
            self.stop_ffmpeg_processes()

    def video_playback_loop(self):
        consecutive_errors = 0
        max_errors = 10
        while True:
            frame_size = self.display_width * self.display_height * 3
            with self.playback_lock:
                if not self.is_playing: break
            
            if self.ffmpeg_process1.poll() is not None or self.ffmpeg_process2.poll() is not None:
                self.handle_playback_end("Video stream ended")
                break

            if self.current_frame_num >= self.total_frames - 1:
                self.handle_playback_end("Finished")
                break
            raw_frame1 = self.ffmpeg_process1.stdout.read(frame_size)
            raw_frame2 = self.ffmpeg_process2.stdout.read(frame_size)
            if not raw_frame1 or not raw_frame2 or len(raw_frame1) != frame_size or len(raw_frame2) != frame_size:
                consecutive_errors += 1
                if consecutive_errors >= max_errors:
                    self.handle_playback_end("Error: Playback stopped")
                    break
                continue
            consecutive_errors = 0
            self.current_frame_num += 1
            if self.duration_known: self.after(0, self.seek_bar.set, self.current_frame_num)
            self.after(0, self.update_frame_display, raw_frame1, raw_frame2)
            self.after(0, self.update_status_bar, "Playing")
            time.sleep(1.0 / self.video_fps)

    def handle_playback_end(self, message):
        with self.playback_lock: self.is_playing = False
        if not self.duration_known:
            self.total_frames = self.current_frame_num
            self.duration_known = True
            self.after(0, lambda: self.seek_bar.config(to=self.total_frames, state=tk.NORMAL))
        self.after(0, lambda: self.play_pause_btn.config(text="▶ Play"))
        self.after(0, self.update_status_bar, message)

    def update_frame_display(self, raw_frame1, raw_frame2):
        """Converts raw frame data to an image and displays it with various comparison modes."""
        if self.video_label.cget("text"): self.video_label.config(text="")
        
        frame1_bgr = np.frombuffer(raw_frame1, dtype='uint8').reshape((self.display_height, self.display_width, 3))
        frame2_bgr = np.frombuffer(raw_frame2, dtype='uint8').reshape((self.display_height, self.display_width, 3))
        
        mode = self.comparison_mode_var.get()
        
        if mode == 'side_by_side':
            combined_frame = frame1_bgr.copy()
            combined_frame[:, self.split_pos:] = frame2_bgr[:, self.split_pos:]
            cv2.line(combined_frame, (self.split_pos, 0), (self.split_pos, self.display_height), (0, 255, 0), 2)
        elif mode == 'overlay':
            alpha = self.split_pos / self.display_width
            combined_frame = cv2.addWeighted(frame1_bgr, 1 - alpha, frame2_bgr, alpha, 0)
        elif mode == 'difference':
            diff = cv2.absdiff(frame1_bgr, frame2_bgr)
            combined_frame = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
            combined_frame = cv2.cvtColor(combined_frame, cv2.COLOR_GRAY2BGR)
        elif mode == 'toggle':
            combined_frame = frame1_bgr if self.split_pos < self.display_width / 2 else frame2_bgr
        
        self._add_video_labels(combined_frame)
        img = cv2.cvtColor(combined_frame, cv2.COLOR_BGR2RGB)
        photo = ImageTk.PhotoImage(image=Image.fromarray(img))
        self.video_label.config(image=photo)
        self.video_label.image = photo

    def _add_video_labels(self, frame):
        """Draws video filenames onto the frame."""
        font, scale, color, thickness = cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1
        shadow = (0, 0, 0)
        cv2.putText(frame, self.video_name1, (11, 31), font, scale, shadow, thickness + 1, cv2.LINE_AA)
        cv2.putText(frame, self.video_name1, (10, 30), font, scale, color, thickness, cv2.LINE_AA)
        (w, _), _ = cv2.getTextSize(self.video_name2, font, scale, thickness)
        x = self.display_width - w - 10
        cv2.putText(frame, self.video_name2, (x + 1, 31), font, scale, shadow, thickness + 1, cv2.LINE_AA)
        cv2.putText(frame, self.video_name2, (x, 30), font, scale, color, thickness, cv2.LINE_AA)

    def _on_video_drag(self, event):
        """Handles dragging on the video to set the split position."""
        if not self.video_path1: return
        self.split_pos = max(0, min(self.display_width, event.x))
        if not self.is_playing: self.display_single_frame(self.current_frame_num)

    def _on_video_click(self, event):
        """Handles clicking on the video to set the split position."""
        if not self.video_path1: return
        self.split_pos = max(0, min(self.display_width, event.x))
        if not self.is_playing: self.display_single_frame(self.current_frame_num)

    def seek_video(self, value):
        if self.seek_bar['state'] == tk.DISABLED: return
        self.current_frame_num = int(float(value))
        if self.is_playing: self.start_ffmpeg_processes(self.current_frame_num)
        else: self.display_single_frame(self.current_frame_num)
        self.update_status_bar("Seek")

    def step_frame(self, direction):
        if self.is_playing or self.play_pause_btn['state'] == tk.DISABLED: return
        if self.seek_bar['state'] == tk.DISABLED and direction < 0: return
        new_frame = self.current_frame_num + direction
        if 0 <= new_frame < self.total_frames:
            self.current_frame_num = new_frame
            if self.duration_known: self.seek_bar.set(self.current_frame_num)
            self.display_single_frame(self.current_frame_num)
            self.update_status_bar("Stepped")

    def display_single_frame(self, frame_number):
        if not self.video_path1 or not self.video_path2: return
        if self.duration_known and not (0 <= frame_number < self.total_frames):
            return

        time_offset1 = frame_number / self.fps1
        time_offset2 = (frame_number + self.video2_offset) / self.fps2
        frame_size = self.display_width * self.display_height * 3
        vf_filter = f"scale={self.display_width}:{self.display_height}"
        common_args = ['-vf', vf_filter, '-f', 'image2pipe', '-vcodec', 'rawvideo', '-pix_fmt', 'bgr24', '-vframes', '1', '-']
        command1 = [self.ffmpeg_path, '-ss', str(time_offset1), '-i', self.video_path1, *common_args]
        command2 = [self.ffmpeg_path, '-ss', str(time_offset2), '-i', self.video_path2, *common_args]
        try:
            raw_frame1 = sp.check_output(command1, stderr=sp.DEVNULL)
            raw_frame2 = sp.check_output(command2, stderr=sp.DEVNULL)
            if len(raw_frame1) == frame_size and len(raw_frame2) == frame_size:
                 self.update_frame_display(raw_frame1, raw_frame2)
        except sp.CalledProcessError as e:
            print(f"FFmpeg error fetching single frame: {e}")
            self.update_status_bar(f"Error seeking to frame {frame_number}")

    def _on_comparison_mode_change(self):
        """Refreshes the frame when the comparison mode changes."""
        if not self.is_playing and self.video_path1 and self.video_path2:
            self.display_single_frame(self.current_frame_num)

    def save_snapshot(self):
        """Saves the current displayed frame as an image."""
        if self.snapshot_btn['state'] == tk.DISABLED or not hasattr(self.video_label, 'image'): return
        file_path = filedialog.asksaveasfilename(parent=self, title="Save Snapshot", defaultextension=".png", filetypes=(("PNG files", "*.png"), ("JPEG files", "*.jpg")))
        if not file_path: return
        try:
            pil_image = ImageTk.getimage(self.video_label.image)
            pil_image.save(file_path)
            self.update_status_bar(f"Snapshot saved to {file_path}")
        except Exception as e:
            messagebox.showerror("Save Error", f"Could not save snapshot:\n{str(e)}")

    def seek_to_start(self):
        if self.seek_bar['state'] == tk.DISABLED: return
        self.seek_video(0)

    def seek_to_end(self):
        if self.seek_bar['state'] == tk.DISABLED: return
        self.seek_video(self.total_frames - 1)

    def _adjust_offset(self, amount):
        if not self.video_path1: return
        self.video2_offset += amount
        self.offset_var.set(str(self.video2_offset))
        if not self.is_playing: self.display_single_frame(self.current_frame_num)
    
    def _validate_offset_input(self, *args):
        if not self.video_path1: return
        try:
            self.video2_offset = int(self.offset_var.get())
            if not self.is_playing: self.display_single_frame(self.current_frame_num)
        except (ValueError, TypeError): pass

    def toggle_fullscreen(self, event=None):
        """Toggles the main window in and out of fullscreen mode."""
        self.is_fullscreen = not self.is_fullscreen
        self.attributes("-fullscreen", self.is_fullscreen)
        if self.is_fullscreen:
            self.controls_frame.grid_remove()
            self.status_bar.grid_remove()
        else:
            self.controls_frame.grid()
            self.status_bar.grid()
        self.handle_resize()

    def exit_fullscreen(self, event=None):
        """Exits fullscreen mode."""
        if self.is_fullscreen:
            self.is_fullscreen = False
            self.attributes("-fullscreen", False)
            self.controls_frame.grid()
            self.status_bar.grid()
            self.handle_resize()

    def _on_window_resize(self, event):
        """Handles window resize events with debouncing."""
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(300, self.handle_resize)

    def handle_resize(self):
        """Recalculates display size and restarts streams if playing."""
        self._resize_job = None
        if not self.video_path1 or not self.video_path2: return
        
        self.update_idletasks()
        canvas_width = self.main_frame.winfo_width()
        canvas_height = self.main_frame.winfo_height()
        
        if not self.is_fullscreen:
            canvas_height -= (self.controls_frame.winfo_height() + self.status_bar.winfo_height() + 20)
        
        new_display_width, new_display_height = 0, 0

        if self.is_fullscreen:
            new_display_width = canvas_width
            new_display_height = canvas_height
        else:
            ar1 = self.video_info1['width'] / self.video_info1['height']
            ar2 = self.video_info2['width'] / self.video_info2['height']
            w, h = (self.video_info1['width'], self.video_info1['height']) if ar1 >= ar2 else (self.video_info2['width'], self.video_info2['height'])
            new_display_width, new_display_height = self.calculate_display_size(w, h, canvas_width, canvas_height)

        if abs(new_display_width - self.display_width) > 1 or abs(new_display_height - self.display_height) > 1:
            self.display_width = new_display_width
            self.display_height = new_display_height
            self.split_pos = self.display_width // 2
            
            was_playing = self.is_playing
            if was_playing:
                with self.playback_lock:
                    self.is_playing = False
            
            self.after(50, lambda: self._restart_after_resize(was_playing))

    def _restart_after_resize(self, was_playing):
        """Safely restarts playback after a resize operation."""
        if was_playing:
            with self.playback_lock:
                self.is_playing = True
            self.start_ffmpeg_processes(self.current_frame_num)
            threading.Thread(target=self.video_playback_loop, daemon=True).start()
        else:
            self.display_single_frame(self.current_frame_num)


    def update_status_bar(self, status_text=""):
        if self.duration_known:
            current_time = format_time(self.current_frame_num / self.video_fps)
            total_time = format_time(self.total_frames / self.video_fps)
            self.status_var.set(f"{status_text} | {current_time} / {total_time}")
        else:
            current_time = format_time(self.current_frame_num / self.video_fps)
            self.status_var.set(f"{status_text} | {current_time} / ??:??")
        self.update_idletasks()

    def on_closing(self):
        print("Closing application...")
        with self.playback_lock: self.is_playing = False
        self.stop_ffmpeg_processes()
        self.destroy()

if __name__ == "__main__":
    print("Starting Video Comparison Tool (FFmpeg Edition)...")
    print("\n--- REQUIREMENTS ---")
    print("1. FFmpeg: Must be installed on your system and accessible via the PATH.")
    print("2. NumPy: Must be installed. 'pip install numpy'")
    print("3. OpenCV & Pillow: 'pip install opencv-python pillow'")
    
    app = VideoComparer()
    app.mainloop()
