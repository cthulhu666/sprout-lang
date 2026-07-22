/* sprout_gfx.c — native graphics backend shim (Milestone 1: raylib).
 *
 * This file is DELIBERATELY outside the runtime .c glob so it links ONLY
 * into graphics programs (via `just run-gfx`), never into the core test suite
 * or the bootstrap seed. It is the thin adapter between Sprout's uniform i64
 * calling convention and raylib's real C API.
 *
 * ABI contract (mirrors the rest of the runtime):
 *   - Every Sprout value crosses as a 64-bit word (`long long`).
 *   - A Sprout `Double` crosses as its raw IEEE-754 bit pattern; reinterpret
 *     with `as_float` (same trick as runtime `double_to_string`).
 *   - A Sprout `String` crosses as a `char*` pointer (see runtime http_request,
 *     whose params are declared `const char*` directly) — so `const char*`
 *     parameters below receive the string data with no conversion.
 *   - Void-semantics functions return `long long` 0 so the Sprout side can bind
 *     them at type `Unit` (mirrors `vector_mutset`).
 */

#include <raylib.h>
#include <rlgl.h>
#include <string.h>
#include <stdlib.h>

/* Reinterpret a Sprout Double (passed as its 64-bit pattern) as a C float. */
static float as_float(long long bits) {
  double d;
  memcpy(&d, &bits, sizeof(double));
  return (float)d;
}

/* Single global 3D camera, mutated via gfx_set_camera and consumed by
 * gfx_frame_begin. raylib passes Camera3D by value across BeginMode3D, which
 * the i64 ABI cannot express — so we hold it C-side and expose decomposed
 * setters. Same pattern will back Model/Texture handles later. */
static Camera3D g_cam;

/* Frame budget: SPROUT_GFX_MAX_FRAMES>0 makes the window auto-close after that
 * many presented frames, so a demo can run non-interactively as a canary. 0 =
 * run until the user closes the window. */
static long long g_frame_counter = 0;
static long long g_max_frames = 0;

/* Dev verification: if SPROUT_GFX_SCREENSHOT=<path> is set, the shim saves one
 * PNG of a warmed-up frame (so an agent/CI can inspect the render without a
 * human at the screen). Path is relative to the working directory. */
static const char *g_screenshot_path = NULL;
static int g_screenshot_done = 0;

/* Model handle registry. raylib returns Model/Texture structs by value, which
 * the i64 ABI cannot carry — so loaded models live here and Sprout holds an
 * integer handle (the array index). Same pattern will back textures later. */
#define GFX_MAX_MODELS 64
static Model g_models[GFX_MAX_MODELS];
static int g_model_count = 0;

long long gfx_open_window(long long w, long long h, const char *title) {
  const char *cap = getenv("SPROUT_GFX_MAX_FRAMES");
  g_max_frames = (cap != NULL) ? atoll(cap) : 0;
  g_frame_counter = 0;
  g_screenshot_path = getenv("SPROUT_GFX_SCREENSHOT");
  g_screenshot_done = 0;

  SetTraceLogLevel(LOG_WARNING);
  InitWindow((int)w, (int)h, title != NULL ? title : "sprout");

  /* Sensible default camera; overridden by gfx_set_camera. */
  g_cam.position = (Vector3){ 6.0f, 6.0f, 6.0f };
  g_cam.target   = (Vector3){ 0.0f, 0.0f, 0.0f };
  g_cam.up       = (Vector3){ 0.0f, 1.0f, 0.0f };
  g_cam.fovy     = 45.0f;
  g_cam.projection = CAMERA_PERSPECTIVE;
  return 0;
}

long long gfx_set_target_fps(long long fps) {
  SetTargetFPS((int)fps);
  return 0;
}

/* Returns 1 when the loop should stop (window closed OR frame budget hit),
 * else 0. Returned as an Int so it crosses the ABI as a plain immediate. */
long long gfx_window_should_close(void) {
  int closed = WindowShouldClose();
  int budget = (g_max_frames > 0 && g_frame_counter >= g_max_frames);
  return (closed || budget) ? 1 : 0;
}

long long gfx_set_camera(long long px, long long py, long long pz,
                         long long tx, long long ty, long long tz,
                         long long fovy) {
  g_cam.position = (Vector3){ as_float(px), as_float(py), as_float(pz) };
  g_cam.target   = (Vector3){ as_float(tx), as_float(ty), as_float(tz) };
  g_cam.fovy     = as_float(fovy);
  return 0;
}

long long gfx_frame_begin(void) {
  BeginDrawing();
  ClearBackground((Color){ 24, 24, 30, 255 });
  BeginMode3D(g_cam);
  return 0;
}

long long gfx_draw_grid(long long slices, long long spacing) {
  DrawGrid((int)slices, as_float(spacing));
  return 0;
}

/* A cube of the given edge size, spun `angle` degrees about the Y axis using
 * raylib's own matrix stack — so the rotation math lives entirely in raylib and
 * the Sprout side supplies only a scalar angle. */
long long gfx_draw_spinning_cube(long long size, long long angle) {
  float s = as_float(size);
  rlPushMatrix();
  rlRotatef(as_float(angle), 0.0f, 1.0f, 0.0f);
  DrawCube((Vector3){ 0.0f, 0.0f, 0.0f }, s, s, s, (Color){ 190, 60, 60, 255 });
  DrawCubeWires((Vector3){ 0.0f, 0.0f, 0.0f }, s, s, s, (Color){ 235, 235, 235, 255 });
  rlPopMatrix();
  return 0;
}

/* Load a model (glTF/GLB/OBJ/IQM/M3D — NOT FBX) and return an integer handle,
 * or -1 if the registry is full. Must be called after gfx_open_window (LoadModel
 * uploads meshes to the GPU, which needs the GL context). */
long long gfx_load_model(const char *path) {
  if (g_model_count >= GFX_MAX_MODELS) return -1;
  int h = g_model_count++;
  Model m = LoadModel(path);
  /* Repair a common FBX->glTF import defect: a diffuse with alpha 0 (fully
   * transparent) renders black/invisible under the default shader. A fully
   * transparent base colour is never intentional, so force it opaque; RGB and
   * already-opaque materials are left untouched. Per-draw tint still applies. */
  for (int i = 0; i < m.materialCount; i++) {
    if (m.materials[i].maps[MATERIAL_MAP_DIFFUSE].color.a == 0) {
      m.materials[i].maps[MATERIAL_MAP_DIFFUSE].color.a = 255;
    }
  }
  g_models[h] = m;
  return h;
}

/* Draw model `handle` at (x,y,z), rotated `angle` degrees about the Y axis,
 * uniformly scaled by `scale`. Out-of-range handles are a no-op. */
long long gfx_draw_model(long long handle, long long x, long long y, long long z,
                         long long angle, long long scale) {
  if (handle < 0 || handle >= g_model_count) return 0;
  Vector3 pos = { as_float(x), as_float(y), as_float(z) };
  float s = as_float(scale);
  DrawModelEx(g_models[(int)handle], pos, (Vector3){ 0.0f, 1.0f, 0.0f },
              as_float(angle), (Vector3){ s, s, s }, WHITE);
  return 0;
}

long long gfx_frame_end(void) {
  EndMode3D();
  EndDrawing();
  g_frame_counter++;
  /* Capture once, a couple of frames in (framebuffer fully composited). */
  if (g_screenshot_path != NULL && !g_screenshot_done && g_frame_counter >= 2) {
    TakeScreenshot(g_screenshot_path);
    g_screenshot_done = 1;
  }
  return 0;
}

long long gfx_close_window(void) {
  CloseWindow();
  return 0;
}
