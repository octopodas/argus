#version 140

uniform sampler2D sampler;   // KWin-provided: the offscreen screen texture
uniform vec2 u_resolution;   // virtual screen size, px
uniform vec2 u_center;       // hole center, px, in texcoord0's coordinate space
uniform float u_radius;      // event-horizon radius, px
uniform float u_strength;    // 0..3 smoothed strength (>1 only grows the radius)
uniform float u_time;        // seconds since effect activation

in vec2 texcoord0;
out vec4 fragColor;

vec3 grab(vec2 px)
{
    return texture(sampler, clamp(px / u_resolution, 0.0, 1.0)).rgb;
}

void main()
{
    vec2 px = texcoord0 * u_resolution;
    vec2 d = px - u_center;
    float r = length(d);
    float rs = u_radius;

    if (r <= rs) { // inside the event horizon
        fragColor = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    // gravitational lensing: pull the sample point toward the hole, ~1/r
    // falloff. Near the horizon r-bend goes negative and samples the far
    // side — the flipped-image look of real lensing.
    float bend = rs * rs / max(r, 1.0);

    // frame dragging: gentle rocking swirl that decays away from the hole
    float vis = clamp(u_strength, 0.0, 1.0); // intensity caps at 1; size keeps growing
    float swirl = 1.2 * vis * exp(-(r - rs) / (2.0 * rs)) * sin(u_time * 0.35);
    float cs = cos(swirl), sn = sin(swirl);
    vec2 dir = normalize(d);
    dir = vec2(dir.x * cs - dir.y * sn, dir.x * sn + dir.y * cs);

    // chromatic aberration: each channel bends slightly differently
    vec3 col;
    col.r = grab(u_center + dir * (r - bend * 1.06)).r;
    col.g = grab(u_center + dir * (r - bend)).g;
    col.b = grab(u_center + dir * (r - bend * 0.94)).b;

    // fade to black approaching the horizon
    col *= smoothstep(rs, rs * 1.25, r);

    // photon ring hugging the horizon, gently shimmering
    float ring = exp(-pow((r - rs * 1.1) / (rs * 0.18), 2.0));
    float shimmer = 0.85 + 0.15 * sin(u_time * 1.7 + atan(d.y, d.x) * 3.0);
    col += vec3(1.0, 0.93, 0.78) * ring * shimmer * vis;

    fragColor = vec4(col, 1.0);
}
