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
static Shader g_cube_shader;   /* vertex-colour lit shader for baked/captured meshes (terrain material) */
static int g_cube_ready = 0;
static int g_loc_view_mode = -1; /* CUBE uViewMode uniform: <0 raw colour (default), 0..3 terrain view */
static int g_loc_levels = -1;    /* CUBE uLevels uniform: elevation band count for the relief ramp */
static int g_loc_time = -1;      /* CUBE uTime uniform: seconds, for animated surfaces (water ribbon) */

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
  "out float fragDepth;\n"           /* view-space depth (clip.w) for distance fog */
  "void main() {\n"
  "    fragTexCoord = vertexTexCoord;\n"
  "    fragNormal = normalize(vec3(matNormal*vec4(vertexNormal, 1.0)));\n"
  "    gl_Position = mvp*vec4(vertexPosition, 1.0);\n"
  "    fragDepth = gl_Position.w;\n"
  "}\n";

/* Exponential distance fog, shared verbatim by all three scene shaders: blend the lit colour
 * toward uFogColor by 1-exp(-density*viewDepth) (Beer-Lambert). uFogDensity 0 disables it (the
 * default), so demos that never call gfx_fog are visually unchanged. Applied in the FORWARD pass
 * (needs per-fragment distance, which the post-process colour texture lacks) — the cheapest AAA
 * cohesion cue, no extra render targets. See docs/gfx-effects-roadmap-v0.md #1. */
#define FOG_GLSL \
  "uniform vec3 uFogColor;\n" \
  "uniform float uFogDensity;\n" \
  "vec3 apply_fog(vec3 col, float depth) {\n" \
  "    float f = 1.0 - exp(-uFogDensity * depth);\n" \
  "    return mix(col, uFogColor, clamp(f, 0.0, 1.0));\n" \
  "}\n"

static const char *LIGHT_FS =
  "#version 330\n"
  "in vec2 fragTexCoord;\n"
  "in vec3 fragNormal;\n"
  "in float fragDepth;\n"
  "uniform sampler2D texture0;\n"
  "uniform vec4 colDiffuse;\n"
  "uniform vec3 lightDir;\n"
  "uniform vec3 lightColor;\n"
  "uniform vec3 ambient;\n"
  FOG_GLSL
  "out vec4 finalColor;\n"
  "void main() {\n"
  "    vec4 base = texture(texture0, fragTexCoord)*colDiffuse;\n"
  "    float diff = max(dot(normalize(fragNormal), normalize(lightDir)), 0.0);\n"
  "    vec3 lit = base.rgb*(ambient + diff*lightColor);\n"
  "    finalColor = vec4(apply_fog(lit, fragDepth), base.a);\n"
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
  "out float fragDepth;\n"
  "out vec2 fragWorldXZ;\n"
  "void main() {\n"
  "    fragNormal = vertexNormal;\n"
  "    fragColor = vertexColor;\n"
  "    fragWorldXZ = vertexPosition.xz;\n"
  "    gl_Position = mvp*vec4(vertexPosition, 1.0);\n"
  "    fragDepth = gl_Position.w;\n"
  "}\n";

/* When uViewMode < 0 (the default) the vertex colour IS the colour — every existing baked-mesh demo
 * (spinning_cube, terrain_demo, …) is unchanged. When uViewMode is 0..3 the terrain-rivers demo has
 * baked per-vertex DATA into the colour attribute (see gfx_capture_quad_data), and this shader
 * decodes it and picks the colour for the current view — so switching views is one uniform, no
 * re-bake. The four ramps below mirror the Sprout demo's original biome/river/dir/relief palettes.
 * uLevels is the elevation band count, for the grey relief ramp. */
static const char *CUBE_FS =
  "#version 330\n"
  "in vec3 fragNormal;\n"
  "in vec4 fragColor;\n"
  "in float fragDepth;\n"
  "in vec2 fragWorldXZ;\n"
  "uniform vec3 lightDir;\n"
  "uniform vec3 lightColor;\n"
  "uniform vec3 ambient;\n"
  "uniform int uViewMode;\n"   /* <0 = raw vertex colour; 0 Main, 1 Relief, 2 Flow, 3 Lakes */
  "uniform int uLevels;\n"     /* elevation band count, for the relief ramp */
  "uniform float uTime;\n"     /* seconds; drives animated surfaces (e.g. the water ribbon) */
  FOG_GLSL
  "out vec4 finalColor;\n"
  "vec3 biome_rgb(int t){\n"
  "    if(t==0) return vec3(40.0,90.0,170.0)/255.0;\n"
  "    else if(t==1) return vec3(205.0,185.0,125.0)/255.0;\n"
  "    else if(t==2) return vec3(70.0,150.0,60.0)/255.0;\n"
  "    else if(t==3) return vec3(28.0,100.0,42.0)/255.0;\n"
  "    else if(t==4) return vec3(212.0,190.0,110.0)/255.0;\n"
  "    else if(t==5) return vec3(120.0,112.0,104.0)/255.0;\n"
  "    else if(t==6) return vec3(236.0,236.0,242.0)/255.0;\n"
  "    else return vec3(172.0,182.0,162.0)/255.0;\n"
  "}\n"
  "vec3 river_rgb(int t){\n"
  "    if(t>=3) return vec3(30.0,75.0,155.0)/255.0;\n"
  "    else if(t==2) return vec3(48.0,105.0,185.0)/255.0;\n"
  "    else return vec3(78.0,140.0,205.0)/255.0;\n"
  "}\n"
  "vec3 dir_rgb(int d){\n"
  "    if(d==1) return vec3(232.0,62.0,62.0)/255.0;\n"
  "    else if(d==2) return vec3(236.0,142.0,40.0)/255.0;\n"
  "    else if(d==3) return vec3(230.0,214.0,52.0)/255.0;\n"
  "    else if(d==4) return vec3(120.0,208.0,66.0)/255.0;\n"
  "    else if(d==5) return vec3(52.0,200.0,158.0)/255.0;\n"
  "    else if(d==6) return vec3(60.0,150.0,236.0)/255.0;\n"
  "    else if(d==7) return vec3(126.0,96.0,222.0)/255.0;\n"
  "    else if(d==8) return vec3(214.0,82.0,200.0)/255.0;\n"
  "    else return vec3(40.0,44.0,52.0)/255.0;\n"
  "}\n"
  /* Unit DOWNSTREAM direction in world XZ for a D8 code (1..8): x=east(dcol), z=south(drow). Lets the
   * water ripple scroll along each river's real flow instead of one global direction. */
  "vec2 flow_dir_vec(int d){\n"
  "    if(d==1) return vec2(0.0,-1.0);\n"
  "    else if(d==2) return normalize(vec2(1.0,-1.0));\n"
  "    else if(d==3) return vec2(1.0,0.0);\n"
  "    else if(d==4) return normalize(vec2(1.0,1.0));\n"
  "    else if(d==5) return vec2(0.0,1.0);\n"
  "    else if(d==6) return normalize(vec2(-1.0,1.0));\n"
  "    else if(d==7) return vec2(-1.0,0.0);\n"
  "    else if(d==8) return normalize(vec2(-1.0,-1.0));\n"
  "    else return vec2(1.0,0.0);\n"
  "}\n"
  "vec3 land_rgb(int band){\n"
  "    int sp = uLevels<=1 ? 1 : uLevels-1;\n"
  "    float g = (60.0 + float(band)*175.0/float(sp))/255.0;\n"
  "    return vec3(g,g,g);\n"
  "}\n"
  /* World-space value noise (hash-lattice, smooth-interpolated) for asset-free procedural terrain
   * texture. Stable under camera motion because it's keyed on world XZ, not screen space. */
  "float hash21(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7)))*43758.5453); }\n"
  "float vnoise(vec2 p){\n"
  "    vec2 i = floor(p); vec2 f = fract(p);\n"
  "    float a = hash21(i);\n"
  "    float b = hash21(i+vec2(1.0,0.0));\n"
  "    float c = hash21(i+vec2(0.0,1.0));\n"
  "    float d = hash21(i+vec2(1.0,1.0));\n"
  "    vec2 u = f*f*(3.0-2.0*f);\n"
  "    return mix(mix(a,b,u.x), mix(c,d,u.x), u.y);\n"
  "}\n"
  /* Procedural triplanar-lite for the Main land surface (asset-free; the demo has no textures and the
   * colour attribute is busy carrying view data, so this modulates the decoded biome colour in place):
   * a SLOPE SPLAT lays rock over steep faces of ANY biome (cliffs read as rock, not stretched biome
   * colour), and two octaves of world-space value noise give the surface material texture instead of a
   * flat patch. Note: this softens SLOPE-driven seams; flat biome-to-biome seams still snap at the tile
   * edge (a fragment only knows its own tile's tag) — that needs baked per-vertex biome weights. */
  "vec3 land_material(vec3 base, vec3 nrm, vec2 wxz){\n"
  "    float slope = 1.0 - clamp(nrm.y, 0.0, 1.0);\n"     /* 0 flat, 1 vertical */
  "    float rockAmt = smoothstep(0.30, 0.62, slope);\n"
  "    vec3 rock = vec3(102.0,95.0,86.0)/255.0;\n"
  "    vec3 col = mix(base, rock, rockAmt);\n"
  "    float n = 0.6*vnoise(wxz*0.20) + 0.4*vnoise(wxz*0.9);\n"  /* two octaves */
  "    return col * (0.86 + 0.24*n);\n"                   /* subtle brightness break-up */
  "}\n"
  "void main() {\n"
  "    vec3 col;\n"
  "    float outA = 1.0;\n"
  "    if(uViewMode < 0){\n"
  "        col = fragColor.rgb;\n"
  "        outA = fragColor.a;\n"
  "    } else {\n"
  "        int tag  = int(fragColor.r*255.0 + 0.5);\n"
  "        int tier = int(fragColor.g*255.0 + 0.5);\n"
  "        int dir  = int(fragColor.b*255.0 + 0.5);\n"
  "        int abnd = int(fragColor.a*255.0 + 0.5);\n"
  "        bool lake = abnd >= 128;\n"
  "        int band = lake ? abnd-128 : abnd;\n"
  "        bool water = (tag == 100);\n"   /* ribbon water-surface marker (baked by the demo) */
  "        if(water && uViewMode == 0){\n"
  "            vec3 base = river_rgb(tier);\n"
  "            vec2 fv = flow_dir_vec(dir);\n"                 /* downstream, in world XZ */
  "            float along = dot(fv, fragWorldXZ);\n"          /* distance along the flow */
  "            float across = dot(vec2(-fv.y, fv.x), fragWorldXZ);\n"
  "            float w1 = sin(along*0.9 - uTime*2.2);\n"       /* crest travels DOWNSTREAM */
  "            float w2 = sin(along*0.5 - uTime*1.3 + across*0.8);\n"
  "            col = base + vec3(0.05,0.06,0.08)*w1 + vec3(0.02,0.03,0.05)*w2;\n"
  "            outA = 0.60;\n"
  "        } else if(lake){\n"
  "            if(uViewMode != 3) discard;\n"
  "            col = vec3(40.0,120.0,205.0)/255.0;\n"
  "        } else if(tier > 0){\n"
  "            col = river_rgb(tier);\n"
  "        } else if(uViewMode == 0){\n"
  "            col = land_material(biome_rgb(tag), normalize(fragNormal), fragWorldXZ);\n"
  "        } else if(uViewMode == 2){\n"
  "            col = dir_rgb(dir);\n"
  "        } else {\n"
  "            col = land_rgb(band);\n"
  "        }\n"
  "    }\n"
  "    float diff = max(dot(normalize(fragNormal), normalize(lightDir)), 0.0);\n"
  "    vec3 lit = col*(ambient + diff*lightColor);\n"
  "    finalColor = vec4(apply_fog(lit, fragDepth), outA);\n"
  "}\n";

/* Instanced-props shader: one DrawMeshInstanced call draws thousands of copies of a model, each
 * positioned by its own `instanceTransform` (a per-instance model matrix uploaded as a vertex
 * attribute), so a whole forest/crowd/field costs a handful of draw calls instead of one DrawModel
 * per object. Colour is the material's colDiffuse — e.g. the Kenney models are vendored as OBJ,
 * whose per-material MTL `Kd` raylib loads into maps[DIFFUSE].color (a tree splits into a bark mesh
 * + a leaves mesh, drawn as separate instanced batches). Lighting matches CUBE/LIGHT: a world-space
 * normal against a fixed key light + ambient. NB: the normal is transformed by `instanceTransform`
 * (NOT the `matNormal` uniform, which raylib binds from the identity matModel under
 * DrawMeshInstanced — using it would light every rotated instance as if unrotated). `wind` is
 * reserved for a future sway. */
static const char *INSTANCE_VS =
  "#version 330\n"
  "in vec3 vertexPosition;\n"
  "in vec3 vertexNormal;\n"
  "in mat4 instanceTransform;\n"
  "uniform mat4 mvp;\n"
  "out vec3 fragNormal;\n"
  "out float fragDepth;\n"
  "void main() {\n"
  "    fragNormal = normalize(vec3(instanceTransform*vec4(vertexNormal, 0.0)));\n"
  "    gl_Position = mvp*instanceTransform*vec4(vertexPosition, 1.0);\n"
  "    fragDepth = gl_Position.w;\n"
  "}\n";

static const char *INSTANCE_FS =
  "#version 330\n"
  "in vec3 fragNormal;\n"
  "in float fragDepth;\n"
  "uniform vec4 colDiffuse;\n"
  "uniform vec3 lightDir;\n"
  "uniform vec3 lightColor;\n"
  "uniform vec3 ambient;\n"
  FOG_GLSL
  "out vec4 finalColor;\n"
  "void main() {\n"
  "    float diff = max(dot(normalize(fragNormal), normalize(lightDir)), 0.0);\n"
  "    vec3 lit = colDiffuse.rgb*(ambient + diff*lightColor);\n"
  "    finalColor = vec4(apply_fog(lit, fragDepth), colDiffuse.a);\n"
  "}\n";

static Shader g_instance_shader;   /* instanced, vertex-colour lit shader for scattered models (trees) */
static int g_instance_ready = 0;

/* Distance fog (docs/gfx-effects-roadmap-v0.md #1). Applied in the scene shaders (uFogDensity
 * defaults to 0 = off, so untouched demos are unchanged); frame_begin also clears the background
 * to the fog colour when on, so distant terrain fades into the horizon with no visible edge. */
static int   g_fog_on = 0;
static int   g_fog_r = 0, g_fog_g = 0, g_fog_b = 0;

/* --- Post-processing -------------------------------------------------------
 * Opt-in full-screen effects. When any effect is enabled (g_post_mask != 0) the
 * per-frame 3D pass is redirected into an off-screen colour+depth target
 * (g_scene_target) in gfx_frame_begin; gfx_frame_end then presents that texture
 * to the screen through ONE full-screen fragment shader (POST_FS) that folds
 * vignette + tonemap + a motion-scaled blur, each gated by a bit of g_post_mask.
 *
 * When g_post_mask == 0 the shim keeps the ORIGINAL direct-to-screen path
 * (BeginDrawing straight to the backbuffer), so the eight existing gfx demos are
 * byte-identical and keep the window's 4x MSAA — LoadRenderTexture yields a
 * non-multisampled target, so redirecting through it would silently drop MSAA.
 * Only opt-in callers pay that tradeoff (and the blur masks the aliasing).
 *
 * present_scene_shaded is the reusable atom: draw the scene texture through POST_FS into the
 * currently-active target, scaled to the window (so it also serves as the SSAA downsample). The
 * caller owns the BeginDrawing/BeginTextureMode bracket. A future multi-pass effect (bloom:
 * bright-pass -> ping-pong blur -> composite, docs/gfx-postprocess-v0.md) is a CHAIN of such
 * passes into scratch targets, with THIS present as the unchanged final link. */
#define GFX_POST_VIGNETTE 1
#define GFX_POST_TONEMAP  2
#define GFX_POST_BLUR     4
/* Blur "amount" (static + motion) is unitless in the API; this converts it to the kernel's
 * pixel radius. ~6 px per unit makes amount 0.5 a clearly-soft focus and 1.0 a strong one,
 * so intuitive small values do something — a RAW sub-pixel radius (amount 0.05 -> 0.05 px)
 * samples the same texel and is invisible, which is why bare tiny amounts looked like no-ops. */
#define GFX_BLUR_PX_PER_UNIT 6.0f

static const char *POST_FS =
  "#version 330\n"
  "in vec2 fragTexCoord;\n"
  "in vec4 fragColor;\n"
  "uniform sampler2D texture0;\n"
  "uniform vec4 colDiffuse;\n"
  "uniform vec2 uResolution;\n"     /* scene target size in pixels, for texel-sized blur steps */
  "uniform int  uMask;\n"           /* GFX_POST_* bit flags */
  "uniform float uVigIntensity;\n"  /* 0 = none .. 1 = corners to black */
  "uniform float uVigRadius;\n"     /* 0..1 normalised distance where darkening starts */
  "uniform float uExposure;\n"      /* pre-tonemap multiply */
  "uniform float uSaturation;\n"    /* post-tonemap grade: 1 = neutral, >1 punchier, <1 toward grey */
  "uniform float uBlur;\n"          /* blur kernel radius in pixels (0 = sharp) */
  "out vec4 finalColor;\n"
  /* 13-tap Gaussian: centre + inner ring (axial ±1, diagonal ±1) + outer axial (±2), spread by
   * `radiusPx` texels. Two rings give a smoother, wider falloff than a 3x3 tent, so even a small
   * radius reads as soft focus. Strength is driven from camera motion (sharp still, softer moving). */
  "vec3 blurred(vec2 uv, float radiusPx) {\n"
  "    vec2 t = radiusPx / uResolution;\n"
  "    vec3 c = texture(texture0, uv).rgb * 0.196;\n"
  "    c += texture(texture0, uv + vec2( t.x, 0.0)).rgb * 0.118;\n"
  "    c += texture(texture0, uv + vec2(-t.x, 0.0)).rgb * 0.118;\n"
  "    c += texture(texture0, uv + vec2(0.0,  t.y)).rgb * 0.118;\n"
  "    c += texture(texture0, uv + vec2(0.0, -t.y)).rgb * 0.118;\n"
  "    c += texture(texture0, uv + vec2( t.x,  t.y)).rgb * 0.059;\n"
  "    c += texture(texture0, uv + vec2(-t.x,  t.y)).rgb * 0.059;\n"
  "    c += texture(texture0, uv + vec2( t.x, -t.y)).rgb * 0.059;\n"
  "    c += texture(texture0, uv + vec2(-t.x, -t.y)).rgb * 0.059;\n"
  "    c += texture(texture0, uv + vec2( 2.0*t.x, 0.0)).rgb * 0.024;\n"
  "    c += texture(texture0, uv + vec2(-2.0*t.x, 0.0)).rgb * 0.024;\n"
  "    c += texture(texture0, uv + vec2(0.0,  2.0*t.y)).rgb * 0.024;\n"
  "    c += texture(texture0, uv + vec2(0.0, -2.0*t.y)).rgb * 0.024;\n"
  "    return c;\n"
  "}\n"
  /* Narkowicz 2015 ACES filmic approximation — a recognisable film tone curve that keeps
   * midtones and rolls off highlights (sky/water glints) instead of clipping them flat. */
  "vec3 aces(vec3 x) {\n"
  "    return clamp((x*(2.51*x + 0.03)) / (x*(2.43*x + 0.59) + 0.14), 0.0, 1.0);\n"
  "}\n"
  "void main() {\n"
  "    vec2 uv = fragTexCoord;\n"
  "    vec3 col = ((uMask & 4) != 0 && uBlur > 0.01) ? blurred(uv, uBlur)\n"
  "                                                  : texture(texture0, uv).rgb;\n"
  "    if ((uMask & 2) != 0) {\n"
  "        col = aces(col * uExposure);\n"
  "        float luma = dot(col, vec3(0.2126, 0.7152, 0.0722));\n"   /* Rec.709 luminance */
  "        col = clamp(mix(vec3(luma), col, uSaturation), 0.0, 1.0);\n"  /* grade toward/away from grey */
  "    }\n"
  "    if ((uMask & 1) != 0) {\n"
  "        float d = length(uv - vec2(0.5)) * 1.41421356;\n"   /* 0 centre .. 1 corner */
  "        float v = smoothstep(uVigRadius, 1.0, d);\n"
  "        col *= (1.0 - v * uVigIntensity);\n"
  "    }\n"
  "    finalColor = vec4(col, 1.0) * colDiffuse * fragColor;\n"
  "}\n";

static RenderTexture2D g_scene_target;
static int g_scene_target_ready = 0;
static Shader g_post_shader;
static int g_post_ready = 0;
static int g_post_mask = 0;                 /* OR of GFX_POST_* — 0 disables the whole path */

/* Supersampling anti-aliasing (SSAA): the scene renders into a target `g_ssaa`× the window on each
 * axis, then downsamples to the window (bilinear) on present — more samples per final pixel, which
 * resolves the minification shimmer of the high-frequency tile terrain (MSAA can't; it only touches
 * silhouettes). g_ssaa 1 = off. The off-screen path activates when post OR ssaa is on (use_offscreen).
 * g_win_* is the window/framebuffer size; g_scene_target is sized g_win_* × g_ssaa. */
static int g_ssaa  = 1;
static int g_win_w = 0, g_win_h = 0;

/* Effect params (defaults are the tasteful settings; setters overwrite them). */
static float g_vig_intensity = 0.55f;
static float g_vig_radius    = 0.55f;
static float g_exposure      = 1.25f;
static float g_saturation    = 1.0f;        /* tone-map grade: 1 = neutral, >1 punchier */
static float g_blur_static   = 0.0f;        /* baseline blur AMOUNT with the camera still (0 = sharp; ~0.5 = clearly soft) */
static float g_blur_gain     = 0.0f;        /* added blur amount per world-unit of per-frame camera motion */
static float g_blur_max      = 1.5f;        /* clamp on total amount (~9 px) so a fast whip can't smear to mush */

/* Altitude-driven baseline: when on, the static component is a linear map of camera height
 * (g_cam.position.y) instead of the constant g_blur_static — motion still adds on top. The
 * caller sets two (altitude, amount) anchors; higher amount at the higher anchor blurs the
 * overview (haze), or reverse the amounts to blur when low. See gfx_post_altitude_blur. */
static int   g_blur_alt_on   = 0;
static float g_alt_lo = 0.0f, g_alt_hi = 1.0f, g_alt_amt_lo = 0.0f, g_alt_amt_hi = 0.0f;

/* Overlay phase: set by gfx_overlay_begin, which presents the scene and switches to 2D screen
 * space so UI draws crisp ON TOP (unblurred). While set, the UI funcs skip their Mode3D toggle
 * and frame_end skips the (already-done) present. 0 = the UI-in-scene path (UI gets post-processed). */
static int   g_overlay = 0;

/* Previous frame's camera, for the motion-scaled blur term (see gfx_frame_end). */
static Vector3 g_prev_cam_pos;
static Vector3 g_prev_cam_target;
static int g_prev_cam_valid = 0;

/* Cached POST_FS uniform locations (GetShaderLocation once, set each frame). */
static int g_loc_resolution, g_loc_mask, g_loc_vig_int, g_loc_vig_rad, g_loc_exposure, g_loc_saturation, g_loc_blur;

/* (Re)allocate the off-screen scene target at (w,h). Bilinear filter so the SSAA present
 * downsamples smoothly. Reused by gfx_supersample to resize when the SSAA factor changes. */
static void alloc_scene_target(int w, int h) {
  if (g_scene_target_ready) { UnloadRenderTexture(g_scene_target); g_scene_target_ready = 0; }
  g_scene_target = LoadRenderTexture(w, h);
  g_scene_target_ready = (g_scene_target.id != 0);
  if (g_scene_target_ready) SetTextureFilter(g_scene_target.texture, TEXTURE_FILTER_BILINEAR);
}

/* True when the scene must render off-screen: any post effect OR supersampling is on. */
static int use_offscreen(void) { return g_scene_target_ready && (g_post_mask != 0 || g_ssaa > 1); }

/* Build the post shader and its render target. Call after InitWindow (needs GL).
 * The window is not resizable, so a fixed size is correct (no resize handling). NULL vs =
 * raylib's default full-screen vertex shader, which supplies fragTexCoord/fragColor for POST_FS. */
static void init_post(void) {
  g_post_shader = LoadShaderFromMemory(NULL, POST_FS);
  if (g_post_shader.id != 0) {
    g_loc_resolution = GetShaderLocation(g_post_shader, "uResolution");
    g_loc_mask       = GetShaderLocation(g_post_shader, "uMask");
    g_loc_vig_int    = GetShaderLocation(g_post_shader, "uVigIntensity");
    g_loc_vig_rad    = GetShaderLocation(g_post_shader, "uVigRadius");
    g_loc_exposure   = GetShaderLocation(g_post_shader, "uExposure");
    g_loc_saturation = GetShaderLocation(g_post_shader, "uSaturation");
    g_loc_blur       = GetShaderLocation(g_post_shader, "uBlur");
    g_post_ready = 1;
  }
  g_win_w = GetRenderWidth();
  g_win_h = GetRenderHeight();
  alloc_scene_target(g_win_w, g_win_h);   /* SSAA 1× until gfx_supersample resizes it */
}

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
    /* Default the terrain-view uniforms so every other cube-shader demo (which never sets them) draws
     * with raw vertex colour: uViewMode = -1. uLevels only matters for the relief ramp; seed a sane 12. */
    g_loc_view_mode = GetShaderLocation(g_cube_shader, "uViewMode");
    g_loc_levels = GetShaderLocation(g_cube_shader, "uLevels");
    g_loc_time = GetShaderLocation(g_cube_shader, "uTime");
    int vm0 = -1, lv0 = 12;
    float t0 = 0.0f;
    SetShaderValue(g_cube_shader, g_loc_view_mode, &vm0, SHADER_UNIFORM_INT);
    SetShaderValue(g_cube_shader, g_loc_levels, &lv0, SHADER_UNIFORM_INT);
    SetShaderValue(g_cube_shader, g_loc_time, &t0, SHADER_UNIFORM_FLOAT);
    g_cube_ready = 1;
  }

  /* Material for baked meshes uses the same cube shader (vertex colour + diffuse relief). */
  g_terrain_material = LoadMaterialDefault();
  if (g_cube_ready) g_terrain_material.shader = g_cube_shader;
  g_terrain_material_ready = 1;

  g_instance_shader = LoadShaderFromMemory(INSTANCE_VS, INSTANCE_FS);
  if (g_instance_shader.id != 0) {
    /* The instance-transform vertex attribute is NOT one of raylib's auto-located standard
     * attributes; DrawMeshInstanced streams per-instance matrices into whatever location this
     * points at, so it must be wired explicitly. (mvp/colDiffuse/vertexColor ARE auto-located
     * by their conventional names when the shader loads.) */
    g_instance_shader.locs[SHADER_LOC_VERTEX_INSTANCETRANSFORM] =
      GetShaderLocationAttrib(g_instance_shader, "instanceTransform");
    SetShaderValue(g_instance_shader, GetShaderLocation(g_instance_shader, "lightDir"), &lightDir, SHADER_UNIFORM_VEC3);
    SetShaderValue(g_instance_shader, GetShaderLocation(g_instance_shader, "lightColor"), &lightColor, SHADER_UNIFORM_VEC3);
    SetShaderValue(g_instance_shader, GetShaderLocation(g_instance_shader, "ambient"), &ambient, SHADER_UNIFORM_VEC3);
    g_instance_ready = 1;
  }
}

/* --- Instanced-props registry: spatial groups, frustum + distance culled ----------------------
 * One system for every repeated prop (trees, rocks, crowds, ...). Instances bucket by a caller-
 * assigned GROUP id (keep it spatially coherent — e.g. one per chunk) x model handle, each group
 * carrying a world AABB, so gfx_draw_instances can skip a whole group when it is off-screen or
 * beyond the LOD cull distance. Drawing per (group,model) would explode the draw-call count, so
 * draw compacts each model's visible instances into one DrawMeshInstanced. Flat, statically sized,
 * zero-initialised (NULL buffers / 0 counts / g_grp_any=0), grown lazily on push. A group can be
 * cleared and re-pushed each frame (dynamic movers) — see gfx_instance_clear. */
#define GFX_MAX_GROUPS 4096
#define GFX_INSTANCE_MODELS 64
#define GFX_INSTANCE_MARGIN 6.0f  /* AABB pad (world units) covering model height/width beyond origin */
static Matrix *g_grp[GFX_MAX_GROUPS * GFX_INSTANCE_MODELS];
static int g_grp_count[GFX_MAX_GROUPS * GFX_INSTANCE_MODELS];
static int g_grp_cap[GFX_MAX_GROUPS * GFX_INSTANCE_MODELS];
static float g_grp_bbmin[GFX_MAX_GROUPS][3];
static float g_grp_bbmax[GFX_MAX_GROUPS][3];
static int g_grp_any[GFX_MAX_GROUPS];

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
                       unsigned char r, unsigned char g, unsigned char b, unsigned char a) {
  int i = g_cap_count;
  g_cap_verts[i*3+0] = x; g_cap_verts[i*3+1] = y; g_cap_verts[i*3+2] = z;
  g_cap_norms[i*3+0] = nx; g_cap_norms[i*3+1] = ny; g_cap_norms[i*3+2] = nz;
  g_cap_cols[i*4+0] = r; g_cap_cols[i*4+1] = g; g_cap_cols[i*4+2] = b; g_cap_cols[i*4+3] = a;
  g_cap_count++;
}

/* A quad as two triangles (v0,v1,v2)+(v0,v2,v3), one flat normal, one RGBA. The alpha channel is a
 * real byte now (not forced to 255): gfx_capture_quad passes 255, but gfx_capture_quad_data smuggles
 * per-vertex DATA (a packed elevation band + lake flag) through it for the shader-decoded views. */
static void cap_quad(const float *v0, const float *v1, const float *v2, const float *v3,
                     float nx, float ny, float nz,
                     unsigned char r, unsigned char g, unsigned char b, unsigned char a) {
  cap_vertex(v0[0],v0[1],v0[2], nx,ny,nz, r,g,b,a);
  cap_vertex(v1[0],v1[1],v1[2], nx,ny,nz, r,g,b,a);
  cap_vertex(v2[0],v2[1],v2[2], nx,ny,nz, r,g,b,a);
  cap_vertex(v0[0],v0[1],v0[2], nx,ny,nz, r,g,b,a);
  cap_vertex(v2[0],v2[1],v2[2], nx,ny,nz, r,g,b,a);
  cap_vertex(v3[0],v3[1],v3[2], nx,ny,nz, r,g,b,a);
}

/* Like cap_quad, but each corner carries its OWN normal (n0..n3) — per-vertex normals, so a
 * continuous heightfield shades smoothly (Gouraud) across tile boundaries instead of per-facet.
 * The two triangles (v0,v1,v2) and (v0,v2,v3) reuse the shared corners' normals. */
static void cap_quad_vn(const float *v0, const float *v1, const float *v2, const float *v3,
                        const float *n0, const float *n1, const float *n2, const float *n3,
                        unsigned char r, unsigned char g, unsigned char b, unsigned char a) {
  cap_vertex(v0[0],v0[1],v0[2], n0[0],n0[1],n0[2], r,g,b,a);
  cap_vertex(v1[0],v1[1],v1[2], n1[0],n1[1],n1[2], r,g,b,a);
  cap_vertex(v2[0],v2[1],v2[2], n2[0],n2[1],n2[2], r,g,b,a);
  cap_vertex(v0[0],v0[1],v0[2], n0[0],n0[1],n0[2], r,g,b,a);
  cap_vertex(v2[0],v2[1],v2[2], n2[0],n2[1],n2[2], r,g,b,a);
  cap_vertex(v3[0],v3[1],v3[2], n3[0],n3[1],n3[2], r,g,b,a);
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
  cap_quad(c010,c011,c111,c110,  0.0f, 1.0f, 0.0f, r,g,b,255);  /* +Y top */
  cap_quad(c000,c100,c101,c001,  0.0f,-1.0f, 0.0f, r,g,b,255);  /* -Y bottom */
  cap_quad(c100,c110,c111,c101,  1.0f, 0.0f, 0.0f, r,g,b,255);  /* +X */
  cap_quad(c000,c001,c011,c010, -1.0f, 0.0f, 0.0f, r,g,b,255);  /* -X */
  cap_quad(c001,c101,c111,c011,  0.0f, 0.0f, 1.0f, r,g,b,255);  /* +Z */
  cap_quad(c000,c010,c110,c100,  0.0f, 0.0f,-1.0f, r,g,b,255);  /* -Z */
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
  init_post();

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

/* --- Raw per-frame input for direct-manipulation camera control -------------
 * These expose live device state raylib reads each frame; there is no Sprout
 * equivalent (term_* / the GUI-button widgets cannot report the wheel, a held
 * key, or the mouse delta). Held state (IsKeyDown/IsMouseButtonDown), NOT the
 * edge variants, so callers can drive continuous motion while a key/button is
 * held. Doubles cross the ABI as their 64-bit IEEE-754 pattern in a GP register
 * (see gfx_get_frame_time). */

/* 1 while `key` (a raylib KEY_* code) is held. */
long long gfx_key_down(long long key) {
  return IsKeyDown((int)key) ? 1 : 0;
}

/* 1 while mouse `button` (a raylib MOUSE_BUTTON_* code) is held. */
long long gfx_mouse_button_down(long long button) {
  return IsMouseButtonDown((int)button) ? 1 : 0;
}

/* Vertical scroll this frame. GetMouseWheelMoveV().y (the vector form), NOT the
 * scalar GetMouseWheelMove(): on macOS a trackpad's two-finger scroll blends X
 * and Y, and the scalar returns whichever axis is larger, so vertical intent can
 * read as horizontal. The vector form keeps the axes separate. */
long long gfx_mouse_wheel_y(void) {
  double d = (double)GetMouseWheelMoveV().y;
  long long bits;
  memcpy(&bits, &d, sizeof(double));
  return bits;
}

/* Mouse movement since last frame, in pixels (raylib GetMouseDelta). */
long long gfx_mouse_delta_x(void) {
  double d = (double)GetMouseDelta().x;
  long long bits;
  memcpy(&bits, &d, sizeof(double));
  return bits;
}

long long gfx_mouse_delta_y(void) {
  double d = (double)GetMouseDelta().y;
  long long bits;
  memcpy(&bits, &d, sizeof(double));
  return bits;
}

/* Mouse cursor position in window pixels — used to gate a drag against a screen
 * region (e.g. orbit only when the cursor is over the 3D view, not the panel). */
long long gfx_mouse_x(void) {
  return (long long)GetMouseX();
}

long long gfx_mouse_y(void) {
  return (long long)GetMouseY();
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
  /* With any post-effect enabled, render the 3D scene into the off-screen target
   * so gfx_frame_end can present it through POST_FS; otherwise draw straight to
   * the backbuffer (keeps 4x MSAA — see the post-processing block). */
  if (use_offscreen()) BeginTextureMode(g_scene_target);
  else BeginDrawing();
  /* Clear to the fog colour when fog is on, so distant terrain fades into the horizon seamlessly. */
  ClearBackground(g_fog_on ? (Color){ (unsigned char)g_fog_r, (unsigned char)g_fog_g, (unsigned char)g_fog_b, 255 }
                           : (Color){ 24, 24, 30, 255 });
  BeginMode3D(g_cam);
  update_frustum();
  return 0;
}

long long gfx_draw_grid(long long slices, long long spacing) {
  DrawGrid((int)slices, as_float(spacing));
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

/* Draw an axis-aligned cube of edge `size` at (x,y,z) in a flat RGB colour (0-255
 * components) — the colour crosses as the cube's per-vertex colour. Two modes:
 *  - inside a mesh_capture_begin/end pair: APPEND the cube's geometry to the capture
 *    (baked into a static mesh, drawn later in one call) — the scalable path;
 *  - otherwise: draw immediately under the active shader. */
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

/* Append one quad to the active capture: corners p0..p3 (two triangles p0,p1,p2 and p0,p2,p3),
 * a face normal (nx,ny,nz), a flat RGB colour. The general mesh-building primitive — a caller bakes
 * arbitrary static geometry from Sprout (e.g. loam.terrain_mesh decomposes a terrain tile into a
 * top quad + wall quads). Coords/normal cross the ABI as Doubles (IEEE-754 bits); rgb as ints.
 * A no-op outside a mesh_capture_begin/end pair. */
long long gfx_capture_quad(long long p0x, long long p0y, long long p0z,
                           long long p1x, long long p1y, long long p1z,
                           long long p2x, long long p2y, long long p2z,
                           long long p3x, long long p3y, long long p3z,
                           long long nx, long long ny, long long nz,
                           long long r, long long g, long long b) {
  if (!g_capturing) return 0;
  cap_reserve(6);
  float v0[3] = { as_float(p0x), as_float(p0y), as_float(p0z) };
  float v1[3] = { as_float(p1x), as_float(p1y), as_float(p1z) };
  float v2[3] = { as_float(p2x), as_float(p2y), as_float(p2z) };
  float v3[3] = { as_float(p3x), as_float(p3y), as_float(p3z) };
  cap_quad(v0, v1, v2, v3, as_float(nx), as_float(ny), as_float(nz),
           (unsigned char)r, (unsigned char)g, (unsigned char)b, 255);
  return 0;
}

/* Append a quad carrying per-vertex DATA (not a colour) for the shader-decoded terrain views: the
 * CUBE shader, when uViewMode >= 0, reads the vertex-colour attribute as a packed payload — R=biome
 * tag, G=flow tier, B=flow direction, A=elevation band with the lake flag in bit 7 — and computes
 * the on-screen colour for the current view from it (see CUBE_FS). This is what lets a view switch be
 * a single uniform change instead of a full re-bake: geometry+data are baked ONCE. tag/tier/dir must
 * be < 128 and band <= 126 (bit 7 is the lake flag). A no-op outside a mesh_capture_begin/end pair. */
long long gfx_capture_quad_data(long long p0x, long long p0y, long long p0z,
                                long long p1x, long long p1y, long long p1z,
                                long long p2x, long long p2y, long long p2z,
                                long long p3x, long long p3y, long long p3z,
                                long long nx, long long ny, long long nz,
                                long long tag, long long tier, long long dir,
                                long long band, long long lake) {
  if (!g_capturing) return 0;
  cap_reserve(6);
  float v0[3] = { as_float(p0x), as_float(p0y), as_float(p0z) };
  float v1[3] = { as_float(p1x), as_float(p1y), as_float(p1z) };
  float v2[3] = { as_float(p2x), as_float(p2y), as_float(p2z) };
  float v3[3] = { as_float(p3x), as_float(p3y), as_float(p3z) };
  unsigned char a = (unsigned char)((band & 0x7F) | (lake ? 0x80 : 0));
  cap_quad(v0, v1, v2, v3, as_float(nx), as_float(ny), as_float(nz),
           (unsigned char)tag, (unsigned char)tier, (unsigned char)dir, a);
  return 0;
}

/* Like gfx_capture_quad_data, but with FOUR per-vertex normals (n0..n3, one per corner) instead of a
 * single face normal. The continuous-terrain bake (examples/gfx/terrain_rivers_demo.sprout) supplies
 * height-gradient normals here so the smooth heightfield shades smoothly rather than per-facet. Data
 * packing (r=tag, g=tier, b=dir, a=band|lake<<7) is identical to gfx_capture_quad_data. */
long long gfx_capture_quad_data_vn(long long p0x, long long p0y, long long p0z,
                                   long long p1x, long long p1y, long long p1z,
                                   long long p2x, long long p2y, long long p2z,
                                   long long p3x, long long p3y, long long p3z,
                                   long long n0x, long long n0y, long long n0z,
                                   long long n1x, long long n1y, long long n1z,
                                   long long n2x, long long n2y, long long n2z,
                                   long long n3x, long long n3y, long long n3z,
                                   long long tag, long long tier, long long dir,
                                   long long band, long long lake) {
  if (!g_capturing) return 0;
  cap_reserve(6);
  float v0[3] = { as_float(p0x), as_float(p0y), as_float(p0z) };
  float v1[3] = { as_float(p1x), as_float(p1y), as_float(p1z) };
  float v2[3] = { as_float(p2x), as_float(p2y), as_float(p2z) };
  float v3[3] = { as_float(p3x), as_float(p3y), as_float(p3z) };
  float n0[3] = { as_float(n0x), as_float(n0y), as_float(n0z) };
  float n1[3] = { as_float(n1x), as_float(n1y), as_float(n1z) };
  float n2[3] = { as_float(n2x), as_float(n2y), as_float(n2z) };
  float n3[3] = { as_float(n3x), as_float(n3y), as_float(n3z) };
  unsigned char a = (unsigned char)((band & 0x7F) | (lake ? 0x80 : 0));
  cap_quad_vn(v0, v1, v2, v3, n0, n1, n2, n3,
              (unsigned char)tag, (unsigned char)tier, (unsigned char)dir, a);
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

/* Free every captured terrain mesh and reset the registry to empty, so the caller can re-bake a
 * fresh set into the same handle range (0..). Only touches CAPTURED meshes (g_meshes); loaded models
 * (g_models) and their GPU instances are a separate registry, left intact — so a view-switching demo
 * can swap the whole baked terrain without disturbing resident trees. The registry is otherwise
 * append-only up to GFX_MAX_MESHES with no way to reclaim a slot, so re-baking without this leaks. */
long long gfx_mesh_reset(void) {
  for (int i = 0; i < g_mesh_count; i++) UnloadMesh(g_meshes[i]);
  g_mesh_count = 0;
  return 0;
}

/* Select which colour a data-encoded terrain mesh (gfx_capture_quad_data) renders: -1 = raw vertex
 * colour (the default; leaves all other cube-shader demos untouched), 0 Main / 1 Relief / 2 Flow /
 * 3 Lakes. This is the whole point of Fix A — a view switch is this one uniform write, no re-bake. */
long long gfx_set_view_mode(long long mode) {
  if (!g_cube_ready) return 0;
  int m = (int)mode;
  SetShaderValue(g_cube_shader, g_loc_view_mode, &m, SHADER_UNIFORM_INT);
  return 0;
}

/* Elevation band count used by the shader's grey relief ramp (Relief/Lakes views). Set once when the
 * demo knows its config; independent of the view mode. */
long long gfx_set_terrain_levels(long long levels) {
  if (!g_cube_ready) return 0;
  int l = (int)levels;
  SetShaderValue(g_cube_shader, g_loc_levels, &l, SHADER_UNIFORM_INT);
  return 0;
}

/* Update the cube shader's animation clock (seconds). General: any uTime-driven surface reads it. */
long long gfx_set_time(long long t) {
  if (!g_cube_ready) return 0;
  float tv = as_float(t);
  SetShaderValue(g_cube_shader, g_loc_time, &tv, SHADER_UNIFORM_FLOAT);
  return 0;
}

/* The engine wall-clock in seconds (raylib GetTime) — a general time source for animation. */
long long gfx_get_time(void) {
  double d = GetTime();
  long long bits;
  memcpy(&bits, &d, sizeof(double));
  return bits;
}

/* --- General transparency state (an app composes its own transparent pass from these) -------------
 * begin_blend_alpha / end_blend wrap standard alpha blending; set_depth_mask toggles depth-buffer
 * WRITES (the depth TEST still applies). Recipe: draw opaque, then begin_blend_alpha(); set_depth_mask(0);
 * <draw transparent, back-to-front-ish>; set_depth_mask(1); end_blend(). Not water-specific. */
long long gfx_begin_blend_alpha(void) {
  BeginBlendMode(BLEND_ALPHA);
  return 0;
}
long long gfx_end_blend(void) {
  EndBlendMode();
  return 0;
}
long long gfx_set_depth_mask(long long on) {
  if (on) rlEnableDepthMask();
  else rlDisableDepthMask();
  return 0;
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

/* Queue one instance of `model` into spatial bucket `group` at (x,y,z), rotated `angle` degrees
 * about Y and uniformly scaled by `scale`. Grows that (group,model) buffer and the group's world
 * AABB (rebuilt from scratch after the group is emptied, so a cleared+re-pushed group re-bounds to
 * its current instances). The transform mirrors DrawModelEx's scale->rotate->translate order.
 * Out-of-range group/model is a no-op. */
long long gfx_instance_push(long long group, long long model, long long x, long long y, long long z,
                        long long angle, long long scale) {
  int grp = (int)group, mdl = (int)model;
  if (grp < 0 || grp >= GFX_MAX_GROUPS || mdl < 0 || mdl >= GFX_INSTANCE_MODELS) return 0;
  int idx = grp * GFX_INSTANCE_MODELS + mdl;
  if (g_grp_count[idx] >= g_grp_cap[idx]) {
    int nc = (g_grp_cap[idx] == 0) ? 64 : g_grp_cap[idx] * 2;
    Matrix *grown = realloc(g_grp[idx], (size_t)nc * sizeof(Matrix));
    if (!grown) { TraceLog(LOG_ERROR, "sprout_gfx: out of memory growing instance group"); return 0; }
    g_grp[idx] = grown; g_grp_cap[idx] = nc;
  }
  float fx = as_float(x), fy = as_float(y), fz = as_float(z), s = as_float(scale);
  Matrix m = MatrixMultiply(MatrixMultiply(MatrixScale(s, s, s),
                                           MatrixRotate((Vector3){ 0.0f, 1.0f, 0.0f }, as_float(angle) * DEG2RAD)),
                            MatrixTranslate(fx, fy, fz));
  g_grp[idx][g_grp_count[idx]++] = m;
  if (!g_grp_any[grp]) {
    g_grp_bbmin[grp][0] = fx; g_grp_bbmin[grp][1] = fy; g_grp_bbmin[grp][2] = fz;
    g_grp_bbmax[grp][0] = fx; g_grp_bbmax[grp][1] = fy; g_grp_bbmax[grp][2] = fz;
    g_grp_any[grp] = 1;
  } else {
    if (fx < g_grp_bbmin[grp][0]) g_grp_bbmin[grp][0] = fx; else if (fx > g_grp_bbmax[grp][0]) g_grp_bbmax[grp][0] = fx;
    if (fy < g_grp_bbmin[grp][1]) g_grp_bbmin[grp][1] = fy; else if (fy > g_grp_bbmax[grp][1]) g_grp_bbmax[grp][1] = fy;
    if (fz < g_grp_bbmin[grp][2]) g_grp_bbmin[grp][2] = fz; else if (fz > g_grp_bbmax[grp][2]) g_grp_bbmax[grp][2] = fz;
  }
  return 0;
}

/* Empty one spatial bucket: zero all its (group,model) instance counts and reset its AABB flag,
 * keeping the allocated buffers for reuse. Dynamic callers (moving crowds) clear their group each
 * frame and re-push; static callers (scenery) never call this. Out-of-range group is a no-op. */
long long gfx_instance_clear(long long group) {
  int grp = (int)group;
  if (grp < 0 || grp >= GFX_MAX_GROUPS) return 0;
  for (int m = 0; m < GFX_INSTANCE_MODELS; m++) g_grp_count[grp * GFX_INSTANCE_MODELS + m] = 0;
  g_grp_any[grp] = 0;
  return 0;
}

/* Scratch for compacting visible instances of one model, and the per-frame visible-group list. */
static Matrix *g_inst_scratch = NULL;
static int g_inst_scratch_cap = 0;
static int g_vis[GFX_MAX_GROUPS];

/* Draw all instance groups [0, group_count), batched ~one DrawMeshInstanced PER MODEL rather than
 * per (group,model): drawing each visible group separately would explode the draw-call count
 * (visible groups x models x meshes) and the per-call overhead would swamp the culling win. So we
 * (1) collect the groups that survive culling, then (2) per model, COMPACT their instances into a
 * scratch buffer and issue one instanced draw — ~one draw/model/frame regardless of group count.
 * A group is drawn only if it passes BOTH culls:
 *   - distance LOD: its centre is within `cull_dist` of the camera eye, so a zoomed-out view where
 *     every instance is far and sub-pixel draws almost none. cull_dist <= 0 disables the distance test.
 *   - frustum: its padded AABB intersects the view (the zoomed-in win).
 * The MODEL count is read from the loaded-model registry; `group_count` is the bucket scan bound.
 * cull_dist crosses the ABI as a Double (its IEEE-754 bit pattern). This is the frame-loop call. */
long long gfx_draw_instances(long long group_count, long long cull_dist) {
  int gc = (int)group_count;
  if (gc > GFX_MAX_GROUPS) gc = GFX_MAX_GROUPS;
  float cull = as_float(cull_dist);
  float cull2 = cull * cull;  /* compare squared distances — no per-group sqrt */
  float ex = g_cam.position.x, ey = g_cam.position.y, ez = g_cam.position.z;
  int nvis = 0;
  for (int g = 0; g < gc; g++) {
    if (!g_grp_any[g]) continue;
    if (cull > 0.0f) {  /* distance LOD: skip groups whose centre is beyond cull_dist */
      float gx = 0.5f * (g_grp_bbmin[g][0] + g_grp_bbmax[g][0]);
      float gy = 0.5f * (g_grp_bbmin[g][1] + g_grp_bbmax[g][1]);
      float gz = 0.5f * (g_grp_bbmin[g][2] + g_grp_bbmax[g][2]);
      float dx = gx - ex, dy = gy - ey, dz = gz - ez;
      if ((dx*dx + dy*dy + dz*dz) > cull2) continue;
    }
    float mn[3] = { g_grp_bbmin[g][0] - GFX_INSTANCE_MARGIN, g_grp_bbmin[g][1] - GFX_INSTANCE_MARGIN, g_grp_bbmin[g][2] - GFX_INSTANCE_MARGIN };
    float mx[3] = { g_grp_bbmax[g][0] + GFX_INSTANCE_MARGIN, g_grp_bbmax[g][1] + GFX_INSTANCE_MARGIN, g_grp_bbmax[g][2] + GFX_INSTANCE_MARGIN };
    if (aabb_in_frustum(mn, mx)) g_vis[nvis++] = g;
  }
  if (nvis == 0) return 0;
  int nmodels = (g_model_count < GFX_INSTANCE_MODELS) ? g_model_count : GFX_INSTANCE_MODELS;
  for (int mdl = 0; mdl < nmodels; mdl++) {
    int total = 0;
    for (int i = 0; i < nvis; i++) total += g_grp_count[g_vis[i] * GFX_INSTANCE_MODELS + mdl];
    if (total == 0) continue;
    if (total > g_inst_scratch_cap) {
      int nc = (g_inst_scratch_cap == 0) ? 1024 : g_inst_scratch_cap;
      while (nc < total) nc *= 2;
      g_inst_scratch = realloc(g_inst_scratch, (size_t)nc * sizeof(Matrix));
      g_inst_scratch_cap = nc;
    }
    int off = 0;
    for (int i = 0; i < nvis; i++) {
      int idx = g_vis[i] * GFX_INSTANCE_MODELS + mdl;
      int cnt = g_grp_count[idx];
      if (cnt > 0) { memcpy(g_inst_scratch + off, g_grp[idx], (size_t)cnt * sizeof(Matrix)); off += cnt; }
    }
    Model model = g_models[mdl];
    for (int i = 0; i < model.meshCount; i++) {
      Material mat = model.materials[model.meshMaterial[i]];
      if (g_instance_ready) mat.shader = g_instance_shader;
      DrawMeshInstanced(model.meshes[i], mat, g_inst_scratch, total);
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
  if (g_overlay) { DrawFPS((int)x, (int)y); return 0; }  /* already in 2D screen space (crisp overlay) */
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
  if (!g_overlay) EndMode3D();   /* in overlay we are already in 2D screen space (crisp UI) */
  DrawRectangleRec(r, hover ? (Color){ 80, 80, 96, 255 } : (Color){ 48, 48, 60, 255 });
  DrawRectangleLinesEx(r, 2.0f, (Color){ 200, 200, 210, 255 });
  int fs = (int)h - 12;
  int tw = MeasureText(label, fs);
  DrawText(label, (int)x + ((int)w - tw) / 2, (int)y + 6, fs, RAYWHITE);
  if (!g_overlay) BeginMode3D(g_cam);
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
  if (!g_overlay) EndMode3D();   /* in overlay we are already in 2D screen space (crisp UI) */
  DrawRectangleRec(r, held  ? (Color){ 110, 120, 150, 255 }
                     : hover ? (Color){ 80, 80, 96, 255 }
                             : (Color){ 48, 48, 60, 255 });
  DrawRectangleLinesEx(r, 2.0f, (Color){ 200, 200, 210, 255 });
  int fs = (int)h - 12;
  int tw = MeasureText(label, fs);
  DrawText(label, (int)x + ((int)w - tw) / 2, (int)y + 6, fs, RAYWHITE);
  if (!g_overlay) BeginMode3D(g_cam);
  return held ? 1 : 0;
}

/* Per-frame camera motion in world units (eye + look-at travel since last frame),
 * driving the motion-scaled blur term. First frame reports 0 (no previous cam). */
static float camera_motion(void) {
  float m = 0.0f;
  if (g_prev_cam_valid) {
    m = Vector3Length(Vector3Subtract(g_cam.position, g_prev_cam_pos))
      + Vector3Length(Vector3Subtract(g_cam.target,   g_prev_cam_target));
  }
  g_prev_cam_pos    = g_cam.position;
  g_prev_cam_target = g_cam.target;
  g_prev_cam_valid  = 1;
  return m;
}

/* Baseline blur amount with the camera still: the altitude map when enabled, else the constant. */
static float blur_baseline(void) {
  if (!g_blur_alt_on) return g_blur_static;
  float t = (g_alt_hi > g_alt_lo) ? (g_cam.position.y - g_alt_lo) / (g_alt_hi - g_alt_lo) : 0.0f;
  if (t < 0.0f) t = 0.0f; else if (t > 1.0f) t = 1.0f;
  return g_alt_amt_lo + t * (g_alt_amt_hi - g_alt_amt_lo);   /* higher amt at hi anchor = blurrier overview */
}

/* Draw the off-screen scene through POST_FS into the CURRENTLY-ACTIVE target (the caller owns the
 * BeginDrawing/BeginTextureMode bracket). Computes this frame's blur (baseline + motion, clamped) and
 * sets the uniforms. Runs the camera bookkeeping via camera_motion(), so it must be called EXACTLY
 * ONCE per frame — the frame_end/overlay_begin guards ensure that. */
static void present_scene_shaded(void) {
  float amount = blur_baseline() + camera_motion() * g_blur_gain;
  if (amount > g_blur_max) amount = g_blur_max;
  float blur = amount * GFX_BLUR_PX_PER_UNIT;   /* amount (unitless) -> kernel radius in pixels */
  /* Run POST_FS only when an effect is enabled; with SSAA alone the present is a plain bilinear
   * downsample (the shader would just be a passthrough). */
  int shade = (g_post_mask != 0 && g_post_ready);
  if (shade) {
    float res[2] = { (float)g_scene_target.texture.width, (float)g_scene_target.texture.height };
    SetShaderValue(g_post_shader, g_loc_resolution, res, SHADER_UNIFORM_VEC2);
    SetShaderValue(g_post_shader, g_loc_mask, &g_post_mask, SHADER_UNIFORM_INT);
    SetShaderValue(g_post_shader, g_loc_vig_int, &g_vig_intensity, SHADER_UNIFORM_FLOAT);
    SetShaderValue(g_post_shader, g_loc_vig_rad, &g_vig_radius, SHADER_UNIFORM_FLOAT);
    SetShaderValue(g_post_shader, g_loc_exposure, &g_exposure, SHADER_UNIFORM_FLOAT);
    SetShaderValue(g_post_shader, g_loc_saturation, &g_saturation, SHADER_UNIFORM_FLOAT);
    SetShaderValue(g_post_shader, g_loc_blur, &blur, SHADER_UNIFORM_FLOAT);
    BeginShaderMode(g_post_shader);
  }
  /* Draw the (possibly SSAA-oversized) scene texture scaled to the window — the bilinear
   * minification IS the supersample resolve. Render textures are y-flipped -> negative src height. */
  DrawTexturePro(g_scene_target.texture,
                 (Rectangle){ 0.0f, 0.0f, (float)g_scene_target.texture.width, -(float)g_scene_target.texture.height },
                 (Rectangle){ 0.0f, 0.0f, (float)g_win_w, (float)g_win_h },
                 (Vector2){ 0.0f, 0.0f }, 0.0f, WHITE);
  if (shade) EndShaderMode();
}

/* Mark the scene -> HUD transition so subsequent UI draws crisp ON TOP of the post-processed
 * scene (unblurred, unvignetted). Call it AFTER the 3D draws and BEFORE the UI (draw_fps/button),
 * then frame_end. It ends the 3D pass, presents the scene (through POST_FS when enabled), and
 * leaves BeginDrawing OPEN so the UI lands on the screen. Optional: a frame that never calls it
 * keeps the old behaviour (UI drawn into the scene, so post-processed too). Once per frame. */
long long gfx_overlay_begin(void) {
  if (g_overlay) return 0;
  EndMode3D();                              /* leave the 3D pass; now in 2D */
  if (use_offscreen()) {
    EndTextureMode();                       /* resolve the off-screen scene... */
    BeginDrawing();                         /* ...and present it to the backbuffer, left OPEN for UI */
    present_scene_shaded();
  }
  /* Non-post: we were already drawing to the screen; EndMode3D above dropped us to 2D for the UI. */
  g_overlay = 1;
  return 0;
}

long long gfx_frame_end(void) {
  if (g_overlay) {
    /* overlay_begin already ended the 3D pass, presented, and left BeginDrawing open;
     * the UI drew on top. Just close the frame. */
    EndDrawing();
    g_overlay = 0;
  } else {
    EndMode3D();
    if (use_offscreen()) {
      EndTextureMode();       /* scene pass done — resolve the off-screen target */
      BeginDrawing();
      present_scene_shaded();  /* present through POST_FS (UI, if any, was drawn into the scene) */
      EndDrawing();
    } else {
      EndDrawing();
    }
  }
  g_frame_counter++;
  /* Capture once, a couple of frames in (framebuffer fully composited). Runs AFTER
   * the final EndDrawing, so it grabs the post-processed screen, not the raw target. */
  if (g_screenshot_path != NULL && !g_screenshot_done && g_frame_counter >= g_screenshot_frame) {
    TakeScreenshot(g_screenshot_path);
    g_screenshot_done = 1;
  }
  return 0;
}

/* --- Post-processing setters -----------------------------------------------
 * Each enables its effect bit AND stores its params, so an app opts in per effect
 * with one call (e.g. gfx.post_vignette(0.5, 0.6)). gfx_post_disable clears the
 * whole path, restoring the direct-to-screen (MSAA) render. All are additive to
 * the binding — adding a future effect (bloom) never changes these signatures. */
long long gfx_post_vignette(long long intensity, long long radius) {
  g_vig_intensity = as_float(intensity);
  g_vig_radius    = as_float(radius);
  g_post_mask |= GFX_POST_VIGNETTE;
  return 0;
}

long long gfx_post_tonemap(long long exposure, long long saturation) {
  g_exposure   = as_float(exposure);
  g_saturation = as_float(saturation);
  g_post_mask |= GFX_POST_TONEMAP;
  return 0;
}

/* Motion-scaled blur: `static_amount` is the blur radius (px) with the camera
 * still; `motion_gain` adds px per world-unit of per-frame camera travel. So the
 * frame is sharp (or lightly soft) at rest and softens while orbiting/zooming. */
long long gfx_post_motion_blur(long long static_amount, long long motion_gain) {
  g_blur_static = as_float(static_amount);
  g_blur_gain   = as_float(motion_gain);
  g_blur_alt_on = 0;              /* a constant baseline overrides any altitude map */
  g_post_mask |= GFX_POST_BLUR;
  return 0;
}

/* Drive the blur BASELINE from camera altitude (g_cam.position.y) instead of a constant: linearly
 * map height in [low_alt, high_alt] to amount in [amt_low, amt_high], clamped outside. amt_high >
 * amt_low blurs the high overview (haze); swap them to blur when low. Motion blur (if set) still
 * adds on top. Enables the blur effect; call after post_motion_blur if BOTH motion + altitude wanted. */
long long gfx_post_altitude_blur(long long low_alt, long long high_alt, long long amt_low, long long amt_high) {
  g_alt_lo     = as_float(low_alt);
  g_alt_hi     = as_float(high_alt);
  g_alt_amt_lo = as_float(amt_low);
  g_alt_amt_hi = as_float(amt_high);
  g_blur_alt_on = 1;
  g_post_mask |= GFX_POST_BLUR;
  return 0;
}

long long gfx_post_disable(void) {
  g_post_mask = 0;
  return 0;
}

/* Exponential distance fog. `density` is the fog coefficient (Beer-Lambert: fog = 1-exp(-density*
 * viewDepth)); 0 disables it. Colour is RGB 0..255 — typically a pale sky/haze tone. Pushes the
 * uniforms to all three scene shaders (set once at setup; not per-frame). frame_begin then clears
 * the background to this colour so distant geometry fades seamlessly into the horizon.
 * Density scale: at density d, the half-fog distance (col 50% toward fog) is ~0.69/d view units. */
long long gfx_fog(long long density, long long r, long long g, long long b) {
  float d = as_float(density);
  Vector3 c = { (float)r / 255.0f, (float)g / 255.0f, (float)b / 255.0f };
  g_fog_on = (d > 0.0f);
  g_fog_r = (int)r; g_fog_g = (int)g; g_fog_b = (int)b;
  if (g_cube_ready) {
    SetShaderValue(g_cube_shader, GetShaderLocation(g_cube_shader, "uFogDensity"), &d, SHADER_UNIFORM_FLOAT);
    SetShaderValue(g_cube_shader, GetShaderLocation(g_cube_shader, "uFogColor"), &c, SHADER_UNIFORM_VEC3);
  }
  if (g_instance_ready) {
    SetShaderValue(g_instance_shader, GetShaderLocation(g_instance_shader, "uFogDensity"), &d, SHADER_UNIFORM_FLOAT);
    SetShaderValue(g_instance_shader, GetShaderLocation(g_instance_shader, "uFogColor"), &c, SHADER_UNIFORM_VEC3);
  }
  if (g_light_ready) {
    SetShaderValue(g_light_shader, GetShaderLocation(g_light_shader, "uFogDensity"), &d, SHADER_UNIFORM_FLOAT);
    SetShaderValue(g_light_shader, GetShaderLocation(g_light_shader, "uFogColor"), &c, SHADER_UNIFORM_VEC3);
  }
  return 0;
}

/* Supersampling AA: render the scene at `factor`× the window on each axis into the off-screen
 * target, then downsample (bilinear) on present. Adds samples per final pixel, which resolves the
 * minification shimmer of high-frequency geometry (the tile terrain) that MSAA can't. factor 1
 * disables it (direct-to-screen, keeps the window's MSAA); 2 is the sweet spot. Clamped [1,4].
 * Cost is factor²× the scene target + fragment shading — cheap on a GPU-light scene. */
long long gfx_supersample(long long factor) {
  int f = (int)factor;
  if (f < 1) f = 1; else if (f > 4) f = 4;
  g_ssaa = f;
  alloc_scene_target(g_win_w * f, g_win_h * f);
  return 0;
}

long long gfx_close_window(void) {
  if (g_scene_target_ready) { UnloadRenderTexture(g_scene_target); g_scene_target_ready = 0; }
  if (g_post_ready) { UnloadShader(g_post_shader); g_post_ready = 0; }
  CloseWindow();
  return 0;
}
