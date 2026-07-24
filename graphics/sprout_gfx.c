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
#include <raymath.h>
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
static long long g_screenshot_frame = 2; /* which frame to capture (SPROUT_GFX_SCREENSHOT_FRAME) */

/* Model handle registry. raylib returns Model/Texture structs by value, which
 * the i64 ABI cannot carry — so loaded models live here and Sprout holds an
 * integer handle (the array index). Grows on demand (doubling) so the count of
 * loaded models has no fixed ceiling — a scene of N animated characters loads N
 * independent Model instances. Same pattern will back textures later. */
static Model *g_models = NULL;
static int g_models_cap = 0;
static int g_model_count = 0;

/* Animation-set registry: each LoadModelAnimations returns an array of clips.
 * Sprout holds an int handle to the set; a clip is (set handle, clip index). */
#define GFX_MAX_ANIMSETS 16
static ModelAnimation *g_animsets[GFX_MAX_ANIMSETS];
static int g_animset_counts[GFX_MAX_ANIMSETS];
static int g_animset_count = 0;

/* Directional-light shader (GLSL 330, desktop GL). raylib auto-binds the
 * standard uniforms by name (mvp/matModel/matNormal/colDiffuse/texture0); this
 * only adds diffuse directional lighting + ambient so models read as 3D instead
 * of flat silhouettes. Applied to every loaded model's materials. */
static Shader g_light_shader;
static int g_light_ready = 0;
static Shader g_cube_shader;   /* vertex-colour lit shader for cubes (immediate + captured mesh) */
static int g_cube_ready = 0;

/* Static baked meshes. One-mesh-per-terrain fit in a handful; per-CHUNK baking (for frustum
 * culling) needs one slot per chunk — a 32x32-chunk map is 1024 — so the cap is generous. */
#define GFX_MAX_MESHES 4096
static Mesh g_meshes[GFX_MAX_MESHES];
/* World-space AABB of each baked mesh, computed at upload, used to frustum-cull it in draw_captured. */
static float g_mesh_min[GFX_MAX_MESHES][3];
static float g_mesh_max[GFX_MAX_MESHES][3];
static int g_mesh_count = 0;
static Material g_terrain_material;
static int g_terrain_material_ready = 0;

/* Six view-frustum planes (a,b,c,d), world space, refreshed each frame in gfx_frame_begin from the
 * current camera. A point is inside when a*x+b*y+c*z+d >= 0 for all six. Used to skip baked meshes
 * whose AABB lies wholly outside the view (draw_captured). */
static float g_frustum[6][4];
static int g_frustum_valid = 0;

/* Mesh capture: gfx_draw_cube appends cube geometry into these growable host arrays instead
 * of drawing, so the SAME per-tile draw loop that renders immediately can also bake a static
 * mesh. Positions/normals are 3 floats/vertex, colours 4 bytes/vertex; non-indexed triangles. */
static int g_capturing = 0;
static float *g_cap_verts = NULL;
static float *g_cap_norms = NULL;
static unsigned char *g_cap_cols = NULL;
static int g_cap_count = 0;   /* vertices captured */
static int g_cap_cap = 0;     /* vertex capacity */

static const char *LIGHT_VS =
  "#version 330\n"
  "in vec3 vertexPosition;\n"
  "in vec2 vertexTexCoord;\n"
  "in vec3 vertexNormal;\n"
  "uniform mat4 mvp;\n"
  "uniform mat4 matModel;\n"
  "uniform mat4 matNormal;\n"
  "out vec2 fragTexCoord;\n"
  "out vec3 fragNormal;\n"
  "void main() {\n"
  "    fragTexCoord = vertexTexCoord;\n"
  "    fragNormal = normalize(vec3(matNormal*vec4(vertexNormal, 1.0)));\n"
  "    gl_Position = mvp*vec4(vertexPosition, 1.0);\n"
  "}\n";

static const char *LIGHT_FS =
  "#version 330\n"
  "in vec2 fragTexCoord;\n"
  "in vec3 fragNormal;\n"
  "uniform sampler2D texture0;\n"
  "uniform vec4 colDiffuse;\n"
  "uniform vec3 lightDir;\n"
  "uniform vec3 lightColor;\n"
  "uniform vec3 ambient;\n"
  "out vec4 finalColor;\n"
  "void main() {\n"
  "    vec4 base = texture(texture0, fragTexCoord)*colDiffuse;\n"
  "    float diff = max(dot(normalize(fragNormal), normalize(lightDir)), 0.0);\n"
  "    vec3 lit = base.rgb*(ambient + diff*lightColor);\n"
  "    finalColor = vec4(lit, base.a);\n"
  "}\n";

/* Diffuse-lit shader for immediate-mode cubes: no texture, colour comes from the
 * per-vertex colour (DrawCube's Color), so a whole terrain of differently-coloured
 * cubes draws under ONE shader activation with no per-cube uniform change. Normals are
 * used in WORLD space (DrawCube emits axis-aligned world normals; no matNormal), so a
 * top face is always lit and side faces darker regardless of camera orbit. */
static const char *CUBE_VS =
  "#version 330\n"
  "in vec3 vertexPosition;\n"
  "in vec3 vertexNormal;\n"
  "in vec4 vertexColor;\n"
  "uniform mat4 mvp;\n"
  "out vec3 fragNormal;\n"
  "out vec4 fragColor;\n"
  "void main() {\n"
  "    fragNormal = vertexNormal;\n"
  "    fragColor = vertexColor;\n"
  "    gl_Position = mvp*vec4(vertexPosition, 1.0);\n"
  "}\n";

static const char *CUBE_FS =
  "#version 330\n"
  "in vec3 fragNormal;\n"
  "in vec4 fragColor;\n"
  "uniform vec3 lightDir;\n"
  "uniform vec3 lightColor;\n"
  "uniform vec3 ambient;\n"
  "out vec4 finalColor;\n"
  "void main() {\n"
  "    float diff = max(dot(normalize(fragNormal), normalize(lightDir)), 0.0);\n"
  "    vec3 lit = fragColor.rgb*(ambient + diff*lightColor);\n"
  "    finalColor = vec4(lit, fragColor.a);\n"
  "}\n";

/* Instanced tree shader: one DrawMeshInstanced call draws thousands of trees, each positioned by
 * its own `instanceTransform` (a per-instance model matrix uploaded as a vertex attribute), so a
 * whole forest costs a handful of draw calls instead of one DrawModel per tree. Colour is the
 * material's colDiffuse — the Kenney models are vendored as OBJ, whose per-material MTL `Kd`
 * raylib loads into maps[DIFFUSE].color (each tree splits into a bark mesh + a leaves mesh, drawn
 * as separate instanced batches). Lighting matches CUBE/LIGHT: a world-space normal against a
 * fixed key light + ambient. NB: the normal is transformed by `instanceTransform` (NOT the
 * `matNormal` uniform, which raylib binds from the identity matModel under DrawMeshInstanced —
 * using it would light every rotated tree as if unrotated). `wind` is reserved for a future sway. */
static const char *TREE_VS =
  "#version 330\n"
  "in vec3 vertexPosition;\n"
  "in vec3 vertexNormal;\n"
  "in mat4 instanceTransform;\n"
  "uniform mat4 mvp;\n"
  "out vec3 fragNormal;\n"
  "void main() {\n"
  "    fragNormal = normalize(vec3(instanceTransform*vec4(vertexNormal, 0.0)));\n"
  "    gl_Position = mvp*instanceTransform*vec4(vertexPosition, 1.0);\n"
  "}\n";

static const char *TREE_FS =
  "#version 330\n"
  "in vec3 fragNormal;\n"
  "uniform vec4 colDiffuse;\n"
  "uniform vec3 lightDir;\n"
  "uniform vec3 lightColor;\n"
  "uniform vec3 ambient;\n"
  "out vec4 finalColor;\n"
  "void main() {\n"
  "    float diff = max(dot(normalize(fragNormal), normalize(lightDir)), 0.0);\n"
  "    vec3 lit = colDiffuse.rgb*(ambient + diff*lightColor);\n"
  "    finalColor = vec4(lit, colDiffuse.a);\n"
  "}\n";

static Shader g_tree_shader;   /* instanced, vertex-colour lit shader for scattered models (trees) */
static int g_tree_ready = 0;

/* Load and configure the lighting shaders. Call after InitWindow (needs GL). */
static void init_lighting(void) {
  Vector3 lightDir   = { 0.5f, 1.0f, 0.4f };   /* points toward an upper-side light */
  Vector3 lightColor = { 1.0f, 0.97f, 0.9f };  /* slightly warm key light */
  Vector3 ambient    = { 0.28f, 0.28f, 0.34f };/* fill so the shadow side isn't black */

  g_light_shader = LoadShaderFromMemory(LIGHT_VS, LIGHT_FS);
  if (g_light_shader.id != 0) {
    SetShaderValue(g_light_shader, GetShaderLocation(g_light_shader, "lightDir"), &lightDir, SHADER_UNIFORM_VEC3);
    SetShaderValue(g_light_shader, GetShaderLocation(g_light_shader, "lightColor"), &lightColor, SHADER_UNIFORM_VEC3);
    SetShaderValue(g_light_shader, GetShaderLocation(g_light_shader, "ambient"), &ambient, SHADER_UNIFORM_VEC3);
    g_light_ready = 1;
  }

  g_cube_shader = LoadShaderFromMemory(CUBE_VS, CUBE_FS);
  if (g_cube_shader.id != 0) {
    SetShaderValue(g_cube_shader, GetShaderLocation(g_cube_shader, "lightDir"), &lightDir, SHADER_UNIFORM_VEC3);
    SetShaderValue(g_cube_shader, GetShaderLocation(g_cube_shader, "lightColor"), &lightColor, SHADER_UNIFORM_VEC3);
    SetShaderValue(g_cube_shader, GetShaderLocation(g_cube_shader, "ambient"), &ambient, SHADER_UNIFORM_VEC3);
    g_cube_ready = 1;
  }

  /* Material for baked meshes uses the same cube shader (vertex colour + diffuse relief). */
  g_terrain_material = LoadMaterialDefault();
  if (g_cube_ready) g_terrain_material.shader = g_cube_shader;
  g_terrain_material_ready = 1;

  g_tree_shader = LoadShaderFromMemory(TREE_VS, TREE_FS);
  if (g_tree_shader.id != 0) {
    /* The instance-transform vertex attribute is NOT one of raylib's auto-located standard
     * attributes; DrawMeshInstanced streams per-instance matrices into whatever location this
     * points at, so it must be wired explicitly. (mvp/colDiffuse/vertexColor ARE auto-located
     * by their conventional names when the shader loads.) */
    g_tree_shader.locs[SHADER_LOC_VERTEX_INSTANCETRANSFORM] =
      GetShaderLocationAttrib(g_tree_shader, "instanceTransform");
    SetShaderValue(g_tree_shader, GetShaderLocation(g_tree_shader, "lightDir"), &lightDir, SHADER_UNIFORM_VEC3);
    SetShaderValue(g_tree_shader, GetShaderLocation(g_tree_shader, "lightColor"), &lightColor, SHADER_UNIFORM_VEC3);
    SetShaderValue(g_tree_shader, GetShaderLocation(g_tree_shader, "ambient"), &ambient, SHADER_UNIFORM_VEC3);
    g_tree_ready = 1;
  }
}

/* Per-model instance registry: for each model handle, a growable array of world transforms. A
 * scene pushes every tree of a given type once at setup (gfx_instance_push), then draws them all
 * each frame with one DrawMeshInstanced per mesh (gfx_draw_instanced). Indexed by model handle;
 * grown lazily so it need not track the model registry's own growth. */
static Matrix **g_inst = NULL;    /* g_inst[h] = transform buffer for model h */
static int *g_inst_count = NULL;  /* live transforms in g_inst[h] */
static int *g_inst_cap = NULL;    /* capacity of g_inst[h] */
static int g_inst_reg = 0;        /* number of handle slots allocated */

/* --- Per-chunk tree instance groups (frustum-culled vegetation) --------------------------------
 * The plain registry above draws every instance of a model every frame. For a large map that means
 * streaming the whole forest even when zoomed into a corner. These GROUPS bucket instances by a
 * caller id (a chunk) x model handle, plus a per-group AABB, so gfx_draw_tree_group can skip a whole
 * chunk's trees when its bounds are off-screen. Flat, statically sized, zero-initialised (NULL
 * buffers / 0 counts / g_tg_any=0), grown lazily on push. */
#define GFX_MAX_TREE_GROUPS 4096
#define GFX_TREE_MODELS 64
#define GFX_TREE_MARGIN 6.0f  /* AABB pad (world units) covering tree height/width beyond trunk base */
static Matrix *g_tg[GFX_MAX_TREE_GROUPS * GFX_TREE_MODELS];
static int g_tg_count[GFX_MAX_TREE_GROUPS * GFX_TREE_MODELS];
static int g_tg_cap[GFX_MAX_TREE_GROUPS * GFX_TREE_MODELS];
static float g_tg_bbmin[GFX_MAX_TREE_GROUPS][3];
static float g_tg_bbmax[GFX_MAX_TREE_GROUPS][3];
static int g_tg_any[GFX_MAX_TREE_GROUPS];

/* Ensure the per-handle registry arrays cover index `h` (slots initialised empty). */
static int inst_reg_reserve(int h) {
  if (h < g_inst_reg) return 1;
  int nr = g_inst_reg == 0 ? 16 : g_inst_reg;
  while (nr <= h) nr *= 2;
  Matrix **ni = realloc(g_inst, (size_t)nr * sizeof(Matrix *));
  int *nc = realloc(g_inst_count, (size_t)nr * sizeof(int));
  int *np = realloc(g_inst_cap, (size_t)nr * sizeof(int));
  if (!ni || !nc || !np) {
    TraceLog(LOG_ERROR, "sprout_gfx: out of memory growing instance registry to %d", nr);
    return 0;
  }
  g_inst = ni; g_inst_count = nc; g_inst_cap = np;
  for (int i = g_inst_reg; i < nr; i++) { g_inst[i] = NULL; g_inst_count[i] = 0; g_inst_cap[i] = 0; }
  g_inst_reg = nr;
  return 1;
}

/* --- Mesh capture helpers --------------------------------------------------- */
static void cap_reserve(int extra) {
  if (g_cap_count + extra <= g_cap_cap) return;
  int nc = (g_cap_cap == 0) ? 8192 : g_cap_cap;
  while (nc < g_cap_count + extra) nc *= 2;
  g_cap_verts = (float *)realloc(g_cap_verts, (size_t)nc * 3 * sizeof(float));
  g_cap_norms = (float *)realloc(g_cap_norms, (size_t)nc * 3 * sizeof(float));
  g_cap_cols  = (unsigned char *)realloc(g_cap_cols, (size_t)nc * 4 * sizeof(unsigned char));
  g_cap_cap = nc;
}

static void cap_vertex(float x, float y, float z, float nx, float ny, float nz,
                       unsigned char r, unsigned char g, unsigned char b) {
  int i = g_cap_count;
  g_cap_verts[i*3+0] = x; g_cap_verts[i*3+1] = y; g_cap_verts[i*3+2] = z;
  g_cap_norms[i*3+0] = nx; g_cap_norms[i*3+1] = ny; g_cap_norms[i*3+2] = nz;
  g_cap_cols[i*4+0] = r; g_cap_cols[i*4+1] = g; g_cap_cols[i*4+2] = b; g_cap_cols[i*4+3] = 255;
  g_cap_count++;
}

/* A quad as two triangles (v0,v1,v2)+(v0,v2,v3), one flat normal, one colour. */
static void cap_quad(const float *v0, const float *v1, const float *v2, const float *v3,
                     float nx, float ny, float nz,
                     unsigned char r, unsigned char g, unsigned char b) {
  cap_vertex(v0[0],v0[1],v0[2], nx,ny,nz, r,g,b);
  cap_vertex(v1[0],v1[1],v1[2], nx,ny,nz, r,g,b);
  cap_vertex(v2[0],v2[1],v2[2], nx,ny,nz, r,g,b);
  cap_vertex(v0[0],v0[1],v0[2], nx,ny,nz, r,g,b);
  cap_vertex(v2[0],v2[1],v2[2], nx,ny,nz, r,g,b);
  cap_vertex(v3[0],v3[1],v3[2], nx,ny,nz, r,g,b);
}

/* Append one axis-aligned cube (36 vertices, per-face flat normals) to the capture. Backface
 * culling is disabled when the baked mesh is drawn, so face winding need not be exact. */
static void cap_cube(float cx, float cy, float cz, float s,
                     unsigned char r, unsigned char g, unsigned char b) {
  float h = s * 0.5f;
  float x0=cx-h, x1=cx+h, y0=cy-h, y1=cy+h, z0=cz-h, z1=cz+h;
  cap_reserve(36);
  float c000[3]={x0,y0,z0}, c001[3]={x0,y0,z1}, c010[3]={x0,y1,z0}, c011[3]={x0,y1,z1};
  float c100[3]={x1,y0,z0}, c101[3]={x1,y0,z1}, c110[3]={x1,y1,z0}, c111[3]={x1,y1,z1};
  cap_quad(c010,c011,c111,c110,  0.0f, 1.0f, 0.0f, r,g,b);  /* +Y top */
  cap_quad(c000,c100,c101,c001,  0.0f,-1.0f, 0.0f, r,g,b);  /* -Y bottom */
  cap_quad(c100,c110,c111,c101,  1.0f, 0.0f, 0.0f, r,g,b);  /* +X */
  cap_quad(c000,c001,c011,c010, -1.0f, 0.0f, 0.0f, r,g,b);  /* -X */
  cap_quad(c001,c101,c111,c011,  0.0f, 0.0f, 1.0f, r,g,b);  /* +Z */
  cap_quad(c000,c010,c110,c100,  0.0f, 0.0f,-1.0f, r,g,b);  /* -Z */
}

/* Capture ONE heightfield tile as a top quad plus only the side walls that are exposed — the
 * scalable alternative to cap_cube for terrain. A stepped cube-terrain buries 5 of every 6 faces
 * under neighbours; emitting just the top and the exposed steps cuts vertices several-fold. Each
 * dN/dS/dE/dW is the world-space drop of that side (0 = neighbour is level-or-higher, so no wall;
 * >0 = neighbour is lower, so a wall drops that far). Directions follow loam's grid convention:
 * +z is south, +x is east, so N is the -z edge and W is the -x edge. Backface culling is off for
 * the baked terrain mesh, so wall winding need not be exact. */
static void cap_tile(float cx, float cz, float top_y, float s,
                     unsigned char r, unsigned char g, unsigned char b,
                     float dn, float ds, float de, float dw) {
  float h = s * 0.5f;
  float x0=cx-h, x1=cx+h, z0=cz-h, z1=cz+h;
  cap_reserve(6 + 6*4);  /* top + up to four walls */
  /* Top face at y = top_y. */
  float t00[3]={x0,top_y,z0}, t01[3]={x0,top_y,z1}, t11[3]={x1,top_y,z1}, t10[3]={x1,top_y,z0};
  cap_quad(t00,t01,t11,t10, 0.0f,1.0f,0.0f, r,g,b);
  /* North wall on the -z edge, dropping dn. */
  if (dn > 0.0f) {
    float a[3]={x0,top_y,z0}, bb[3]={x1,top_y,z0}, c[3]={x1,top_y-dn,z0}, d[3]={x0,top_y-dn,z0};
    cap_quad(a,bb,c,d, 0.0f,0.0f,-1.0f, r,g,b);
  }
  /* South wall on the +z edge. */
  if (ds > 0.0f) {
    float a[3]={x0,top_y,z1}, bb[3]={x1,top_y,z1}, c[3]={x1,top_y-ds,z1}, d[3]={x0,top_y-ds,z1};
    cap_quad(a,bb,c,d, 0.0f,0.0f,1.0f, r,g,b);
  }
  /* East wall on the +x edge. */
  if (de > 0.0f) {
    float a[3]={x1,top_y,z0}, bb[3]={x1,top_y,z1}, c[3]={x1,top_y-de,z1}, d[3]={x1,top_y-de,z0};
    cap_quad(a,bb,c,d, 1.0f,0.0f,0.0f, r,g,b);
  }
  /* West wall on the -x edge. */
  if (dw > 0.0f) {
    float a[3]={x0,top_y,z0}, bb[3]={x0,top_y,z1}, c[3]={x0,top_y-dw,z1}, d[3]={x0,top_y-dw,z0};
    cap_quad(a,bb,c,d, -1.0f,0.0f,0.0f, r,g,b);
  }
}

long long gfx_open_window(long long w, long long h, const char *title) {
  const char *cap = getenv("SPROUT_GFX_MAX_FRAMES");
  g_max_frames = (cap != NULL) ? atoll(cap) : 0;
  g_frame_counter = 0;
  g_screenshot_path = getenv("SPROUT_GFX_SCREENSHOT");
  g_screenshot_done = 0;
  const char *shot_frame = getenv("SPROUT_GFX_SCREENSHOT_FRAME");
  g_screenshot_frame = (shot_frame != NULL) ? atoll(shot_frame) : 2;

  SetTraceLogLevel(LOG_WARNING);
  /* 4x MSAA — must be hinted before InitWindow; smooths jagged polygon/grid edges. */
  SetConfigFlags(FLAG_MSAA_4X_HINT);
  InitWindow((int)w, (int)h, title != NULL ? title : "sprout");
  init_lighting();

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

/* Real seconds since the previous frame (raylib GetFrameTime). Returned as a
 * Sprout Double, so the float is widened to double and crosses as its 64-bit
 * IEEE-754 pattern (the inverse of `as_float`). loam.driver consumes it to run a
 * fixed-timestep simulation independent of the render framerate. */
long long gfx_get_frame_time(void) {
  double d = (double)GetFrameTime();
  long long bits;
  memcpy(&bits, &d, sizeof(double));
  return bits;
}

/* 1 on the frame the Space key transitions to pressed (edge, not held) — the
 * shape a toggle wants. IsKeyPressed already fires once per press. */
long long gfx_space_pressed(void) {
  return IsKeyPressed(KEY_SPACE) ? 1 : 0;
}

long long gfx_set_camera(long long px, long long py, long long pz,
                         long long tx, long long ty, long long tz,
                         long long fovy) {
  g_cam.position = (Vector3){ as_float(px), as_float(py), as_float(pz) };
  g_cam.target   = (Vector3){ as_float(tx), as_float(ty), as_float(tz) };
  g_cam.fovy     = as_float(fovy);
  return 0;
}

/* Extract the six world-space frustum planes from the current camera (Gribb-Hartmann). Must run
 * inside BeginMode3D so rlGetMatrix* return this frame's matrices. combo = proj * view; raylib's
 * MatrixMultiply(a,b) yields b*a mathematically, so MatrixMultiply(view, proj) is proj*view. A
 * raylib Matrix stores element (row i, col j) in field index i + 4*j, so row i is
 * (m[i], m[i+4], m[i+8], m[i+12]); the planes are row3 +/- row{0,1,2}. */
static void update_frustum(void) {
  Matrix view = rlGetMatrixModelview();
  Matrix proj = rlGetMatrixProjection();
  Matrix M = MatrixMultiply(view, proj);
  /* raylib Matrix fields are named by LOGICAL position: m0,m4,m8,m12 = row 0, etc. (the struct
   * stores them column-major, so &M.m0 in MEMORY order is m0,m4,m8,m12,m1,... — indexing a float*
   * by k would read the transpose. Access by field name to get true rows.) */
  float r0[4] = { M.m0, M.m4, M.m8,  M.m12 };
  float r1[4] = { M.m1, M.m5, M.m9,  M.m13 };
  float r2[4] = { M.m2, M.m6, M.m10, M.m14 };
  float r3[4] = { M.m3, M.m7, M.m11, M.m15 };
  for (int k = 0; k < 4; k++) {
    g_frustum[0][k] = r3[k] + r0[k];  /* left   */
    g_frustum[1][k] = r3[k] - r0[k];  /* right  */
    g_frustum[2][k] = r3[k] + r1[k];  /* bottom */
    g_frustum[3][k] = r3[k] - r1[k];  /* top    */
    g_frustum[4][k] = r3[k] + r2[k];  /* near   */
    g_frustum[5][k] = r3[k] - r2[k];  /* far    */
  }
  g_frustum_valid = 1;
}

/* 1 if the AABB [mn,mx] is at least partly inside the frustum; 0 if it lies wholly outside (safe to
 * cull). Tests each plane against the box's most-positive corner: if that corner is behind a plane,
 * every corner is, so the box is outside. Conservative (may keep some just-outside boxes) — fine. */
static int aabb_in_frustum(const float *mn, const float *mx) {
  if (!g_frustum_valid) return 1;
  for (int p = 0; p < 6; p++) {
    float a = g_frustum[p][0], b = g_frustum[p][1], c = g_frustum[p][2], d = g_frustum[p][3];
    float px = (a >= 0.0f) ? mx[0] : mn[0];
    float py = (b >= 0.0f) ? mx[1] : mn[1];
    float pz = (c >= 0.0f) ? mx[2] : mn[2];
    if (a*px + b*py + c*pz + d < 0.0f) return 0;
  }
  return 1;
}

long long gfx_frame_begin(void) {
  BeginDrawing();
  ClearBackground((Color){ 24, 24, 30, 255 });
  BeginMode3D(g_cam);
  update_frustum();
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
 * or -1 only if the registry cannot grow (out of memory — logged loudly). Must
 * be called after gfx_open_window (LoadModel uploads meshes to the GPU, which
 * needs the GL context). */
long long gfx_load_model(const char *path) {
  if (g_model_count >= g_models_cap) {
    int new_cap = g_models_cap == 0 ? 16 : g_models_cap * 2;
    Model *grown = realloc(g_models, (size_t)new_cap * sizeof(Model));
    if (!grown) {
      TraceLog(LOG_ERROR, "sprout_gfx: out of memory growing model registry to %d", new_cap);
      return -1;
    }
    g_models = grown;
    g_models_cap = new_cap;
  }
  int h = g_model_count++;
  /* raylib's glTF loader warns "Colors attribute data already loaded" once per
   * mesh carrying a COLOR_0 vertex attribute (the Kenney models do). It is benign,
   * and with one model loaded per crowd entity it floods the log. Silence warnings
   * for the load only, then restore LOG_WARNING so real warnings elsewhere show. */
  SetTraceLogLevel(LOG_ERROR);
  Model m = LoadModel(path);
  SetTraceLogLevel(LOG_WARNING);
  /* Repair a common FBX->glTF import defect: a diffuse with alpha 0 (fully
   * transparent) renders black/invisible under the default shader. A fully
   * transparent base colour is never intentional, so force it opaque; RGB and
   * already-opaque materials are left untouched. Per-draw tint still applies. */
  for (int i = 0; i < m.materialCount; i++) {
    if (m.materials[i].maps[MATERIAL_MAP_DIFFUSE].color.a == 0) {
      m.materials[i].maps[MATERIAL_MAP_DIFFUSE].color.a = 255;
    }
    if (g_light_ready) m.materials[i].shader = g_light_shader;
  }
  g_models[h] = m;
  return h;
}

/* Begin/end a terrain batch: activate the vertex-colour cube shader ONCE around many
 * gfx_draw_cube calls, so a whole terrain draws under a single shader activation (one
 * batch, no per-cube GL state change). Call gfx_terrain_begin before the tile loop and
 * gfx_terrain_end after; between them, gfx_draw_cube colours come from the cube's own
 * Color. Outside a begin/end pair, gfx_draw_cube falls back to the default shader. */
long long gfx_terrain_begin(void) {
  if (g_cube_ready) BeginShaderMode(g_cube_shader);
  return 0;
}

long long gfx_terrain_end(void) {
  if (g_cube_ready) EndShaderMode();
  return 0;
}

/* Draw an axis-aligned cube of edge `size` at (x,y,z) in a flat RGB colour (0-255
 * components) — the colour crosses as the cube's per-vertex colour. Two modes:
 *  - inside a mesh_capture_begin/end pair: APPEND the cube's geometry to the capture
 *    (baked into a static mesh, drawn later in one call) — the scalable path;
 *  - otherwise: draw immediately (inside a terrain_begin/end pair for the cube shader). */
long long gfx_draw_cube(long long x, long long y, long long z, long long size,
                        long long r, long long g, long long b) {
  float s = as_float(size);
  if (g_capturing) {
    cap_cube(as_float(x), as_float(y), as_float(z), s,
             (unsigned char)r, (unsigned char)g, (unsigned char)b);
    return 0;
  }
  Vector3 pos = { as_float(x), as_float(y), as_float(z) };
  Color col = { (unsigned char)r, (unsigned char)g, (unsigned char)b, 255 };
  DrawCube(pos, s, s, s, col);
  return 0;
}

/* Capture one heightfield tile (top + exposed side walls) into the current mesh. Only meaningful
 * inside a mesh_capture_begin/end pair — a no-op otherwise (there is no immediate-mode fallback;
 * this exists specifically to bake large terrain cheaply). (cx,cz) is the tile centre, top_y the
 * world height of its top face, size the tile edge; r,g,b the flat colour; dN/dS/dE/dW the exposed
 * side drops (0 = no wall). See cap_tile for the geometry. */
long long gfx_capture_tile(long long cx, long long cz, long long top_y, long long size,
                           long long r, long long g, long long b,
                           long long dn, long long ds, long long de, long long dw) {
  if (!g_capturing) return 0;
  cap_tile(as_float(cx), as_float(cz), as_float(top_y), as_float(size),
           (unsigned char)r, (unsigned char)g, (unsigned char)b,
           as_float(dn), as_float(ds), as_float(de), as_float(dw));
  return 0;
}

/* Begin capturing gfx_draw_cube geometry into a mesh instead of drawing it. */
long long gfx_mesh_capture_begin(void) {
  g_capturing = 1;
  g_cap_count = 0;
  return 0;
}

/* Finish a capture: upload the accumulated geometry as a static GPU mesh and return its
 * handle (-1 if the registry is full or nothing was captured). The mesh owns its own copy of
 * the arrays; the capture buffers are reused for the next capture. */
long long gfx_mesh_capture_end(void) {
  g_capturing = 0;
  if (g_mesh_count >= GFX_MAX_MESHES || g_cap_count == 0) return -1;
  Mesh m = { 0 };
  m.vertexCount = g_cap_count;
  m.triangleCount = g_cap_count / 3;
  m.vertices = (float *)malloc((size_t)g_cap_count * 3 * sizeof(float));
  m.normals  = (float *)malloc((size_t)g_cap_count * 3 * sizeof(float));
  m.colors   = (unsigned char *)malloc((size_t)g_cap_count * 4 * sizeof(unsigned char));
  memcpy(m.vertices, g_cap_verts, (size_t)g_cap_count * 3 * sizeof(float));
  memcpy(m.normals,  g_cap_norms, (size_t)g_cap_count * 3 * sizeof(float));
  memcpy(m.colors,   g_cap_cols,  (size_t)g_cap_count * 4 * sizeof(unsigned char));
  UploadMesh(&m, false);
  int h = g_mesh_count++;
  g_meshes[h] = m;
  /* World-space AABB over the captured vertices, for frustum culling in draw_captured. */
  float mnx = g_cap_verts[0], mny = g_cap_verts[1], mnz = g_cap_verts[2];
  float mxx = mnx, mxy = mny, mxz = mnz;
  for (int i = 1; i < g_cap_count; i++) {
    float x = g_cap_verts[i*3+0], y = g_cap_verts[i*3+1], z = g_cap_verts[i*3+2];
    if (x < mnx) mnx = x; else if (x > mxx) mxx = x;
    if (y < mny) mny = y; else if (y > mxy) mxy = y;
    if (z < mnz) mnz = z; else if (z > mxz) mxz = z;
  }
  g_mesh_min[h][0] = mnx; g_mesh_min[h][1] = mny; g_mesh_min[h][2] = mnz;
  g_mesh_max[h][0] = mxx; g_mesh_max[h][1] = mxy; g_mesh_max[h][2] = mxz;
  return h;
}

/* Draw a filled horizontal (XZ) plane centred at (x,y,z) of size (sx,sz) in RGBA (0-255). With
 * a < 255 it blends over whatever is already drawn — a translucent water surface at sea level.
 * Draw it AFTER the terrain so depth-testing lets peaks above it poke through while it covers
 * the valleys below. */
long long gfx_draw_plane(long long x, long long y, long long z, long long sx, long long sz,
                         long long r, long long g, long long b, long long a) {
  DrawPlane((Vector3){ as_float(x), as_float(y), as_float(z) },
            (Vector2){ as_float(sx), as_float(sz) },
            (Color){ (unsigned char)r, (unsigned char)g, (unsigned char)b, (unsigned char)a });
  return 0;
}

/* Draw a baked mesh once. Frustum-culled: a mesh whose AABB lies wholly outside the current view is
 * skipped — so a caller can hand every chunk mesh to this each frame and pay only for the visible
 * ones (the win when zoomed in). Backface culling is off so hand-authored winding need not be exact.
 * Out-of-range handles are a no-op. */
long long gfx_draw_captured(long long handle) {
  if (handle < 0 || handle >= g_mesh_count) return 0;
  if (!aabb_in_frustum(g_mesh_min[(int)handle], g_mesh_max[(int)handle])) return 0;
  rlDisableBackfaceCulling();
  DrawMesh(g_meshes[(int)handle], g_terrain_material, MatrixIdentity());
  rlEnableBackfaceCulling();
  return 0;
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

/* Queue one instance of model `handle` at (x,y,z), rotated `angle` degrees about Y, uniformly
 * scaled by `scale`. Call once per object at setup; the transform is built here (mirroring
 * DrawModelEx's scale->rotate->translate order) and stored, then replayed cheaply every frame by
 * gfx_draw_instanced. Out-of-range handles are a no-op. */
long long gfx_instance_push(long long handle, long long x, long long y, long long z,
                            long long angle, long long scale) {
  if (handle < 0 || handle >= g_model_count) return 0;
  int h = (int)handle;
  if (!inst_reg_reserve(h)) return 0;
  if (g_inst_count[h] >= g_inst_cap[h]) {
    int nc = g_inst_cap[h] == 0 ? 256 : g_inst_cap[h] * 2;
    Matrix *grown = realloc(g_inst[h], (size_t)nc * sizeof(Matrix));
    if (!grown) { TraceLog(LOG_ERROR, "sprout_gfx: out of memory growing instance buffer"); return 0; }
    g_inst[h] = grown; g_inst_cap[h] = nc;
  }
  float s = as_float(scale);
  Matrix m = MatrixMultiply(MatrixMultiply(MatrixScale(s, s, s),
                                           MatrixRotate((Vector3){ 0.0f, 1.0f, 0.0f }, as_float(angle) * DEG2RAD)),
                            MatrixTranslate(as_float(x), as_float(y), as_float(z)));
  g_inst[h][g_inst_count[h]++] = m;
  return 0;
}

/* Draw every queued instance of model `handle` in one DrawMeshInstanced call per mesh, under the
 * instanced tree shader (world-normal lit, per-vertex colour x material colour). The material is
 * copied by value with its shader overridden, so the stored model is left untouched. A no-op for
 * out-of-range handles or handles with no queued instances. */
long long gfx_draw_instanced(long long handle) {
  if (handle < 0 || handle >= g_model_count) return 0;
  int h = (int)handle;
  if (h >= g_inst_reg || g_inst_count[h] == 0) return 0;
  Model model = g_models[h];
  for (int i = 0; i < model.meshCount; i++) {
    Material mat = model.materials[model.meshMaterial[i]];
    if (g_tree_ready) mat.shader = g_tree_shader;
    DrawMeshInstanced(model.meshes[i], mat, g_inst[h], g_inst_count[h]);
  }
  return 0;
}

/* Queue one tree instance into group `group` (a chunk) for model `model`, growing that (group,model)
 * buffer and the group's world AABB. Bucketing by group lets gfx_draw_tree_group frustum-cull a
 * whole chunk's trees. Out-of-range group/model is a no-op. */
long long gfx_tree_push(long long group, long long model, long long x, long long y, long long z,
                        long long angle, long long scale) {
  int grp = (int)group, mdl = (int)model;
  if (grp < 0 || grp >= GFX_MAX_TREE_GROUPS || mdl < 0 || mdl >= GFX_TREE_MODELS) return 0;
  int idx = grp * GFX_TREE_MODELS + mdl;
  if (g_tg_count[idx] >= g_tg_cap[idx]) {
    int nc = (g_tg_cap[idx] == 0) ? 64 : g_tg_cap[idx] * 2;
    Matrix *grown = realloc(g_tg[idx], (size_t)nc * sizeof(Matrix));
    if (!grown) { TraceLog(LOG_ERROR, "sprout_gfx: out of memory growing tree group"); return 0; }
    g_tg[idx] = grown; g_tg_cap[idx] = nc;
  }
  float fx = as_float(x), fy = as_float(y), fz = as_float(z), s = as_float(scale);
  Matrix m = MatrixMultiply(MatrixMultiply(MatrixScale(s, s, s),
                                           MatrixRotate((Vector3){ 0.0f, 1.0f, 0.0f }, as_float(angle) * DEG2RAD)),
                            MatrixTranslate(fx, fy, fz));
  g_tg[idx][g_tg_count[idx]++] = m;
  if (!g_tg_any[grp]) {
    g_tg_bbmin[grp][0] = fx; g_tg_bbmin[grp][1] = fy; g_tg_bbmin[grp][2] = fz;
    g_tg_bbmax[grp][0] = fx; g_tg_bbmax[grp][1] = fy; g_tg_bbmax[grp][2] = fz;
    g_tg_any[grp] = 1;
  } else {
    if (fx < g_tg_bbmin[grp][0]) g_tg_bbmin[grp][0] = fx; else if (fx > g_tg_bbmax[grp][0]) g_tg_bbmax[grp][0] = fx;
    if (fy < g_tg_bbmin[grp][1]) g_tg_bbmin[grp][1] = fy; else if (fy > g_tg_bbmax[grp][1]) g_tg_bbmax[grp][1] = fy;
    if (fz < g_tg_bbmin[grp][2]) g_tg_bbmin[grp][2] = fz; else if (fz > g_tg_bbmax[grp][2]) g_tg_bbmax[grp][2] = fz;
  }
  return 0;
}

/* Scratch for compacting visible instances of one model, and the per-frame visible-group list. */
static Matrix *g_tree_scratch = NULL;
static int g_tree_scratch_cap = 0;
static int g_vis[GFX_MAX_TREE_GROUPS];

/* Draw all tree groups [0, group_count), frustum-culled, in ~one DrawMeshInstanced PER MODEL rather
 * than per (group,model). Naively drawing each visible group separately explodes the draw-call count
 * (dozens of visible groups x models x meshes) and the per-call overhead swamps the culling win — so
 * instead we (1) collect the visible groups once, then (2) per model, COMPACT their instances into a
 * scratch buffer and issue a single instanced draw. Result: ~30 draws/frame regardless of how many
 * groups are visible, over only the on-screen instances. This is the call the frame loop makes. */
long long gfx_draw_trees_culled(long long group_count) {
  int gc = (int)group_count;
  if (gc > GFX_MAX_TREE_GROUPS) gc = GFX_MAX_TREE_GROUPS;
  int nvis = 0;
  for (int g = 0; g < gc; g++) {
    if (!g_tg_any[g]) continue;
    float mn[3] = { g_tg_bbmin[g][0] - GFX_TREE_MARGIN, g_tg_bbmin[g][1] - GFX_TREE_MARGIN, g_tg_bbmin[g][2] - GFX_TREE_MARGIN };
    float mx[3] = { g_tg_bbmax[g][0] + GFX_TREE_MARGIN, g_tg_bbmax[g][1] + GFX_TREE_MARGIN, g_tg_bbmax[g][2] + GFX_TREE_MARGIN };
    if (aabb_in_frustum(mn, mx)) g_vis[nvis++] = g;
  }
  if (nvis == 0) return 0;
  int nmodels = (g_model_count < GFX_TREE_MODELS) ? g_model_count : GFX_TREE_MODELS;
  for (int mdl = 0; mdl < nmodels; mdl++) {
    int total = 0;
    for (int i = 0; i < nvis; i++) total += g_tg_count[g_vis[i] * GFX_TREE_MODELS + mdl];
    if (total == 0) continue;
    if (total > g_tree_scratch_cap) {
      int nc = (g_tree_scratch_cap == 0) ? 1024 : g_tree_scratch_cap;
      while (nc < total) nc *= 2;
      g_tree_scratch = realloc(g_tree_scratch, (size_t)nc * sizeof(Matrix));
      g_tree_scratch_cap = nc;
    }
    int off = 0;
    for (int i = 0; i < nvis; i++) {
      int idx = g_vis[i] * GFX_TREE_MODELS + mdl;
      int cnt = g_tg_count[idx];
      if (cnt > 0) { memcpy(g_tree_scratch + off, g_tg[idx], (size_t)cnt * sizeof(Matrix)); off += cnt; }
    }
    Model model = g_models[mdl];
    for (int i = 0; i < model.meshCount; i++) {
      Material mat = model.materials[model.meshMaterial[i]];
      if (g_tree_ready) mat.shader = g_tree_shader;
      DrawMeshInstanced(model.meshes[i], mat, g_tree_scratch, total);
    }
  }
  return 0;
}

/* Load an animation set from a file (glTF/GLB/IQM/M3D). The skeleton must match
 * the model that will play it (raylib skins by bone index). Returns a set
 * handle, or -1 if the registry is full. */
long long gfx_load_animations(const char *path) {
  if (g_animset_count >= GFX_MAX_ANIMSETS) return -1;
  int count = 0;
  ModelAnimation *a = LoadModelAnimations(path, &count);
  int h = g_animset_count++;
  g_animsets[h] = a;
  g_animset_counts[h] = count;
  return h;
}

long long gfx_animation_count(long long set) {
  if (set < 0 || set >= g_animset_count) return 0;
  return g_animset_counts[(int)set];
}

/* Number of keyframes in a clip — the modulus for looping the playhead. */
long long gfx_animation_keyframes(long long set, long long index) {
  if (set < 0 || set >= g_animset_count) return 0;
  if (index < 0 || index >= g_animset_counts[(int)set]) return 0;
  return g_animsets[(int)set][(int)index].keyframeCount;
}

/* Pose `model` to clip (set,index) at `frame` (fractional frames interpolate). */
long long gfx_update_animation(long long model, long long set, long long index, long long frame) {
  if (model < 0 || model >= g_model_count) return 0;
  if (set < 0 || set >= g_animset_count) return 0;
  if (index < 0 || index >= g_animset_counts[(int)set]) return 0;
  UpdateModelAnimation(g_models[(int)model], g_animsets[(int)set][(int)index], as_float(frame));
  return 0;
}

/* Draw raylib's built-in FPS counter at (x,y) as a 2D screen-space overlay.
 * Callable mid-frame: the frame is inside BeginMode3D, so drop to 2D for the
 * text and re-enter 3D — frame_end's EndMode3D stays balanced. */
long long gfx_draw_fps(long long x, long long y) {
  EndMode3D();
  DrawFPS((int)x, (int)y);
  BeginMode3D(g_cam);
  return 0;
}

/* Immediate-mode button: draw a labelled box as a 2D overlay and return 1 if the
 * left mouse button was pressed inside it this frame (else 0). Draws with a
 * hover highlight. Self-brackets out of Mode3D like gfx_draw_fps; hit-testing is
 * coordinate math, mode-independent. */
long long gfx_button(long long x, long long y, long long w, long long h, const char *label) {
  Rectangle r = { (float)x, (float)y, (float)w, (float)h };
  int hover = CheckCollisionPointRec(GetMousePosition(), r);
  int clicked = hover && IsMouseButtonPressed(MOUSE_BUTTON_LEFT);
  EndMode3D();
  DrawRectangleRec(r, hover ? (Color){ 80, 80, 96, 255 } : (Color){ 48, 48, 60, 255 });
  DrawRectangleLinesEx(r, 2.0f, (Color){ 200, 200, 210, 255 });
  int fs = (int)h - 12;
  int tw = MeasureText(label, fs);
  DrawText(label, (int)x + ((int)w - tw) / 2, (int)y + 6, fs, RAYWHITE);
  BeginMode3D(g_cam);
  return clicked ? 1 : 0;
}

/* Like gfx_button, but HELD: returns 1 on every frame the left mouse button is
 * down inside the box (IsMouseButtonDown, not ...Pressed), so holding it repeats
 * an action frame-by-frame. This is what continuous camera control (rotate / pan
 * / zoom) wants — one press orbits smoothly rather than nudging once per click.
 * The box brightens while held so the press reads visually. */
long long gfx_button_held(long long x, long long y, long long w, long long h, const char *label) {
  Rectangle r = { (float)x, (float)y, (float)w, (float)h };
  int hover = CheckCollisionPointRec(GetMousePosition(), r);
  int held = hover && IsMouseButtonDown(MOUSE_BUTTON_LEFT);
  EndMode3D();
  DrawRectangleRec(r, held  ? (Color){ 110, 120, 150, 255 }
                     : hover ? (Color){ 80, 80, 96, 255 }
                             : (Color){ 48, 48, 60, 255 });
  DrawRectangleLinesEx(r, 2.0f, (Color){ 200, 200, 210, 255 });
  int fs = (int)h - 12;
  int tw = MeasureText(label, fs);
  DrawText(label, (int)x + ((int)w - tw) / 2, (int)y + 6, fs, RAYWHITE);
  BeginMode3D(g_cam);
  return held ? 1 : 0;
}

long long gfx_frame_end(void) {
  EndMode3D();
  EndDrawing();
  g_frame_counter++;
  /* Capture once, a couple of frames in (framebuffer fully composited). */
  if (g_screenshot_path != NULL && !g_screenshot_done && g_frame_counter >= g_screenshot_frame) {
    TakeScreenshot(g_screenshot_path);
    g_screenshot_done = 1;
  }
  return 0;
}

long long gfx_close_window(void) {
  CloseWindow();
  return 0;
}
