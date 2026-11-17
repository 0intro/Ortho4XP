import array
import numpy
import O4_UI_Utils as UI

def recut_water_tris(node_coords, tri_idx, tri_types):

    assert(len(node_coords) % 5 == 0)
    nbr_nodes = len(node_coords) // 5;
    assert(len(tri_idx) % 3 == 0)
    assert(len(tri_idx) // 3 == len(tri_types))
    nbr_tris  = len(tri_types)

    # Fill node types using a boolean or on a bit field.
    # Coastline nodes are those that will have both a land and water bits set. 
    # Assumes that tri_types[n] is already in the range {0, 1, 2}
    node_types = numpy.zeros(nbr_nodes, dtype = numpy.uint8)
    for i in range(nbr_tris):
        t = 1 << tri_types[i]
        node_types[tri_idx[3 * i + 0]] |= t 
        node_types[tri_idx[3 * i + 1]] |= t 
        node_types[tri_idx[3 * i + 2]] |= t
    node_is_coast = ((node_types & 1) != 0) & ((node_types & 6) != 0)

    # Count the number of coastline tris (i.e. with at least one coastline vtx)
    # The latter are the only ones with a potential recut).
    tri_is_coast = numpy.zeros(nbr_tris, dtype = bool)
    for i in range(nbr_tris):
        tri_is_coast[i] |= node_is_coast[tri_idx[3 * i + 0]]
        tri_is_coast[i] |= node_is_coast[tri_idx[3 * i + 1]]
        tri_is_coast[i] |= node_is_coast[tri_idx[3 * i + 2]]
    coast_tri_count = numpy.sum(tri_is_coast)

    # Classify edges of coastline tris
    edge_type = {}
    for i in range(nbr_tris):
        if not tri_is_coast[i]:
            continue
        t = 1 << tri_types[i]
        (a,b,c) = tri_idx[3 * i: 3 * i + 3]
        for (m,n) in ((a,b), (b,c), (c,a)):
            if (m,n) in edge_type or (n,m) in edge_type:
                edge_type[(m,n)] |= t
                edge_type[(n,m)] |= t
            else:
                edge_type[(m,n)] = t
                edge_type[(n,m)] = t

    # Copy and increase size for node_coords, node_is_coast
    node_max_count = nbr_nodes + 3 * coast_tri_count
    node_coords = numpy.resize(node_coords, 5 * node_max_count)
    node_is_coast = numpy.resize(node_is_coast, node_max_count)

    # Cut edges that need to : i.e. have no land bit but their 
    # end-points are coastline, except on the tile boundary, where the
    # neighbouring tile has no vertex in their middle.
    def on_boundary(a, b):
        (xa, ya) = node_coords[5 * a : 5 * a + 2]
        (xb, yb) = node_coords[5 * b : 5 * b + 2]
        return (xa == xb and xa == round(xa)) or (ya == yb and ya == round(ya))

    edge_cut = {}
    next_n = nbr_nodes
    for ((a, b), t) in edge_type.items():
        if b < a:
            # do not do twice the work we have both pairs anyway
            continue
        if (
            (t & 1 == 0)
            and node_is_coast[a]
            and node_is_coast[b]
            and not on_boundary(a, b)
        ):
            edge_cut[(a,b)] = next_n
            edge_cut[(b,a)] = next_n
            node_coords[5 * next_n: 5 * next_n + 5] = (
                    node_coords[5 * a: 5 * a + 5] +
                    node_coords[5 * b: 5 * b + 5]
                    ) / 2.0
            node_is_coast[next_n] = False
            next_n += 1

    # Copy and increase size for tri_idx, tri_types
    tri_max_count = nbr_tris + 3 * coast_tri_count
    tri_idx = numpy.resize(tri_idx, 3 * tri_max_count)
    tri_types = numpy.resize(tri_types, tri_max_count)

    next_t = nbr_tris

    # Cut the tris that need to.
    # When cutting a tri, its place in tri_idx is taken by one of the 
    # newly created ones, and the other ones are appended at the end.
    for i in range(nbr_tris):
        if not tri_types[i] or not tri_is_coast[i]:
            continue
        (a,b,c) = tri_idx[3 * i: 3 * i + 3]
        C = (a,b) in edge_cut
        A = (b,c) in edge_cut
        B = (c,a) in edge_cut
        cuts = A + B + C
        if (not cuts):
            # We might still need to cut the tri if all its vertices are
            # coastline, its edges then being coastline or on the tile
            # boundary.
            if not (node_is_coast[a] and node_is_coast[b] and node_is_coast[c]):
                continue
            # Cut the tri at its barycenter
            node_coords[5 * next_n: 5 * next_n + 5] = (
                   node_coords[5 * a: 5 * a + 5] + 
                   node_coords[5 * b: 5 * b + 5] +
                   node_coords[5 * c: 5 * c + 5]
                   ) / 3.0
            node_is_coast[next_n] = False
            tri_idx[3 * i: 3 * i + 3] = (a,b,next_n)
            tri_idx[3 * next_t: 3 * next_t + 6] = (b,c,next_n,c,a,next_n)
            tri_types[next_t] = tri_types[next_t + 1] = tri_types[i]
            next_n += 1
            next_t += 2
        else:
            # Make a drawing ! a, b, c are the triangles vertices, A, B, C
            # are the potential cut vertices opposed respectively to a, b, c.
            L = array.array('i')
            L.append(a)
            if C:
                s1 = len(L)
                L.append(edge_cut[(a,b)])
            else:
                s2 = len(L) - 1
            L.append(b)
            if A:
                s1 = len(L)
                L.append(edge_cut[(b,c)])
            else:
                s2 = len(L) - 1
            L.append(c)
            if B:
                s1 = len(L)
                L.append(edge_cut[(c,a)])
            else:
                s2 = len(L) - 1
            L = L + L
            
            if (cuts == 2):
                # By def of s2 (x,y) is the one edge with no cut
                # Make a drawing !
                (x,y,z,t,u) = L[s2:s2+5]
                tri_idx[3 * i: 3 * i + 3] = (x,y,z)
                tri_idx[3 * next_t: 3 * next_t + 6] = (z,t,u,x,z,u)
                tri_types[next_t: next_t + 2] = tri_types[i]
                next_t += 2
            elif (cuts == 1):
                # By def of s1 x is the cut node
                # Make a drawing !
                (x,y,z,t) = L[s1:s1+4]
                tri_idx[3 * i: 3 * i + 3] = (x,y,z)
                tri_idx[3 * next_t: 3 * next_t + 3] = (x,z,t)
                tri_types[next_t] = tri_types[i]
                next_t += 1
            else: # cuts == 3
                # By def of s1 x is some cut node
                # Make a drawing !
                (x,y,z,t,u,v) = L[s1:s1+6]
                tri_idx[3 * i: 3 * i + 3] = (x,y,z)
                tri_idx[3 * next_t: 3 * next_t + 9] = (z,t,u,u,v,x,x,z,u)
                tri_types[next_t: next_t + 3] = tri_types[i]
                next_t += 3

    nbr_nodes = next_n
    assert(node_max_count >= nbr_nodes)
    node_coords = numpy.resize(node_coords, 5 * nbr_nodes)
    node_is_coast = numpy.resize(node_is_coast, nbr_nodes)
    
    nbr_tris = next_t
    assert(tri_max_count >= nbr_tris)
    tri_idx = numpy.resize(tri_idx, 3 * nbr_tris)
    tri_types = numpy.resize(tri_types, nbr_tris)

    return (nbr_nodes, node_coords, node_is_coast,
            nbr_tris, tri_idx, tri_types)


# The elevation and sea_level rasters of a DSF hold 1201 by 1201 posts,
# every 1/1200 of a degree from the south west corner of the tile, row 0
# to the south, as those of X-Plane's Global Scenery do. Coordinates below
# are in units of 1/1200 of a degree from that corner.
posts = 1201
step = 1200
# The distance to land counts the water of the tiles around within pad
# cells, as Laminar's rasters match across tile edges.
pad = 40


def boxes(x, y, tri_idx, n, offset, margin, chunk=1 << 21):
    # Yields, in chunks, the points (i + offset, j + offset) of an n by n
    # grid in the bounding box of each triangle, grown by margin, as the
    # arrays (t, i, j) of the triangle and of the point.
    tx = x[tri_idx].reshape(-1, 3)
    ty = y[tri_idx].reshape(-1, 3)
    eps = 1e-9
    i0 = numpy.maximum(numpy.ceil(tx.min(1) - offset - margin - eps), 0)
    i1 = numpy.minimum(numpy.floor(tx.max(1) - offset + margin + eps), n - 1)
    j0 = numpy.maximum(numpy.ceil(ty.min(1) - offset - margin - eps), 0)
    j1 = numpy.minimum(numpy.floor(ty.max(1) - offset + margin + eps), n - 1)
    w = numpy.maximum(i1 - i0 + 1, 0).astype(numpy.int64)
    h = numpy.maximum(j1 - j0 + 1, 0).astype(numpy.int64)
    (i0, j0) = (i0.astype(numpy.int64), j0.astype(numpy.int64))
    count = w * h
    cum = numpy.cumsum(count)
    start = 0
    while start < len(count):
        base = cum[start - 1] if start else 0
        end = int(numpy.searchsorted(cum, base + chunk, side="right"))
        end = min(max(end, start + 1), len(count))
        c = count[start:end]
        t = numpy.repeat(numpy.arange(start, end), c)
        k = numpy.arange(len(t)) - numpy.repeat(numpy.cumsum(c) - c, c)
        yield (t, i0[t] + k % w[t], j0[t] + k // w[t])
        start = end


def inside(x, y, tri_idx, t, px, py):
    # The barycentric weights of the points (px, py) in their triangles t,
    # and whether each point lies in its triangle.
    a = tri_idx[3 * t]
    b = tri_idx[3 * t + 1]
    c = tri_idx[3 * t + 2]
    d = (y[b] - y[c]) * (x[a] - x[c]) + (x[c] - x[b]) * (y[a] - y[c])
    with numpy.errstate(divide="ignore", invalid="ignore"):
        l0 = ((y[b] - y[c]) * (px - x[c]) + (x[c] - x[b]) * (py - y[c])) / d
        l1 = ((y[c] - y[a]) * (px - x[c]) + (x[a] - x[c]) * (py - y[c])) / d
        l2 = 1 - l0 - l1
    eps = -1e-9
    ok = (d != 0) & (l0 >= eps) & (l1 >= eps) & (l2 >= eps)
    return (l0, l1, l2, ok)


def nearest(x, y, z, tri_idx, t, px, py):
    # The height of the point of each triangle t nearest to (px, py), and
    # its distance: inside the triangle, by its barycentric weights, and
    # beyond, along its nearest edge.
    (l0, l1, l2, ok) = inside(x, y, tri_idx, t, px, py)
    (a, b, c) = (tri_idx[3 * t], tri_idx[3 * t + 1], tri_idx[3 * t + 2])
    with numpy.errstate(invalid="ignore"):
        h = numpy.where(ok, l0 * z[a] + l1 * z[b] + l2 * z[c], 0.0)
    d = numpy.where(ok, 0.0, numpy.inf)
    for (p, q) in ((a, b), (b, c), (c, a)):
        (dx, dy) = (x[q] - x[p], y[q] - y[p])
        length = dx * dx + dy * dy
        with numpy.errstate(divide="ignore", invalid="ignore"):
            s = ((px - x[p]) * dx + (py - y[p]) * dy) / length
        s = numpy.where(length > 0, numpy.clip(s, 0, 1), 0.0)
        e = numpy.hypot(px - x[p] - s * dx, py - y[p] - s * dy)
        closer = ~ok & (e < d)
        d = numpy.where(closer, e, d)
        h = numpy.where(closer, z[p] + s * (z[q] - z[p]), h)
    return (h, d)


def water_triangles(tri_idx, tri_types):
    tri_idx = numpy.asarray(tri_idx, dtype=numpy.int64)
    water = numpy.flatnonzero(numpy.asarray(tri_types) != 0)
    return tri_idx.reshape(-1, 3)[water].ravel()


def water_cells(node_coords, tri_idx, tri_types, lat, lon, cells):
    # Marks in cells, cells of 1/1200 of a degree from pad cells south and
    # west of the tile whose south west corner is lat, lon, those whose
    # centres lie in the water triangles of a mesh, of that tile or of
    # another.
    n = cells.shape[0]
    x = (node_coords[0::5] - lon) * step + pad
    y = (node_coords[1::5] - lat) * step + pad
    wet_idx = water_triangles(tri_idx, tri_types)
    flat = cells.reshape(-1)
    for (t, i, j) in boxes(x, y, wet_idx, n, 0.5, 0.0):
        ok = inside(x, y, wet_idx, t, i + 0.5, j + 0.5)[3]
        flat[j[ok] * n + i[ok]] = True


def rasterize(node_coords, tri_idx, tri_types, lat, lon):
    # Returns the height of the mesh at each post, and at each post the
    # lowest water surface within one post, inf where there is none.
    x = (node_coords[0::5] - lon) * step
    y = (node_coords[1::5] - lat) * step
    z = node_coords[2::5]
    tri_idx = numpy.asarray(tri_idx, dtype=numpy.int64)
    elevation = numpy.full(posts * posts, numpy.nan)
    for (t, i, j) in boxes(x, y, tri_idx, posts, 0.0, 0.0):
        (l0, l1, l2, ok) = inside(x, y, tri_idx, t, i, j)
        (t, l0, l1, l2) = (t[ok], l0[ok], l1[ok], l2[ok])
        elevation[j[ok] * posts + i[ok]] = (
            l0 * z[tri_idx[3 * t]]
            + l1 * z[tri_idx[3 * t + 1]]
            + l2 * z[tri_idx[3 * t + 2]]
        )
    elevation = elevation.reshape(posts, posts)
    wet_idx = water_triangles(tri_idx, tri_types)
    surface = numpy.full(posts * posts, numpy.inf)
    for (t, i, j) in boxes(x, y, wet_idx, posts, 0.0, 1.0):
        (h, d) = nearest(x, y, z, wet_idx, t, i, j)
        near = d <= 1
        numpy.minimum.at(surface, j[near] * posts + i[near], h[near])
    return (elevation, surface.reshape(posts, posts))


def wet_posts(cells):
    # A post is water when one of the four cells around it is, as in
    # Laminar's water DEM.
    c = numpy.pad(cells, 1)
    return c[:-1, :-1] | c[:-1, 1:] | c[1:, :-1] | c[1:, 1:]


def distance(wet):
    # The distance in posts from each water post to the nearest other one,
    # 0 at the others, by the single round of four sweeps of a first order
    # Eikonal solver in which Laminar's CalcWaterBathymetry (DEMAlgs.cpp)
    # computes it. A sweep visits the posts row by row, each from the
    # values the sweep has already set before it, so the posts of one
    # anti-diagonal go together. Beyond the grid lies nothing, as there.
    (h, w) = wet.shape
    d = numpy.full((h + 2, w + 2), numpy.inf)
    d[1:-1, 1:-1] = numpy.where(wet, numpy.inf, 0.0)
    for (sy, sx) in ((1, 1), (1, -1), (-1, -1), (-1, 1)):
        v = d[::sy, ::sx]
        for k in range(2, h + w + 1):
            iy = numpy.arange(max(1, k - w), min(h, k - 1) + 1)
            ix = k - iy
            cur = v[iy, ix]
            a = numpy.minimum(v[iy, ix - 1], v[iy, ix + 1])
            b = numpy.minimum(v[iy - 1, ix], v[iy + 1, ix])
            with numpy.errstate(invalid="ignore"):
                e = numpy.abs(a - b)
                new = numpy.where(
                    e < 1,
                    (a + b + numpy.sqrt(2 - e * e)) / 2,
                    numpy.minimum(a, b) + 1,
                )
            v[iy, ix] = numpy.where(cur > 0, numpy.minimum(cur, new), cur)
    return d[1:-1, 1:-1]


def to_int16(r):
    # X-Plane's tools truncate toward zero when they write a raster.
    return numpy.clip(numpy.trunc(r), -32768, 32767).astype(numpy.int16)


def floor(elevation, d, surface):
    # The sea_level raster: each post lies min(50, 1 + 4 * (d - 1)) m below
    # the surface of the water, as in CalcWaterBathymetry, which is the
    # lowest water within one post, or the post itself beyond. The other
    # posts, at d = 0, lie 1 m below it.
    depth = numpy.minimum(50, 1 + 4 * numpy.maximum(0, d - 1))
    return to_int16(numpy.minimum(elevation, surface) - depth)


def rasters(node_coords, tri_idx, tri_types, lat, lon, around=None, custom=None):
    # Returns the elevation and sea_level rasters of the mesh, int16.
    # around holds the water cells of the tiles around, as water_cells
    # marks them, and water where nothing is known of them, which then
    # brings no land, as beyond the edge of CalcWaterBathymetry. custom
    # holds the elevations of a floor at the posts, NaN where it has none:
    # at water posts they replace those of the rule, within 1 m to 50 m
    # below the surface.
    (elevation, surface) = rasterize(
        node_coords, tri_idx, tri_types, lat, lon
    )
    missing = numpy.isnan(elevation)
    if missing.any():
        UI.lvprint(
            0,
            "     ERROR: the mesh leaves",
            int(missing.sum()),
            "posts of the elevation raster uncovered.",
        )
        elevation[missing] = numpy.nanmean(elevation) if (~missing).any() else 0
    elevation = to_int16(elevation)
    n = step + 2 * pad
    cells = numpy.ones((n, n), dtype=bool) if around is None else around.copy()
    cells[pad : pad + step, pad : pad + step] = False
    water_cells(node_coords, tri_idx, tri_types, lat, lon, cells)
    d = distance(wet_posts(cells))[pad : pad + posts, pad : pad + posts]
    f = floor(elevation, d, surface)
    if custom is not None:
        ok = (d > 0) & numpy.isfinite(custom)
        surf = numpy.minimum(elevation, surface)[ok]
        f[ok] = to_int16(numpy.clip(custom[ok], surf - 50, surf - 1))
    return (elevation, f)


def raster_at_posts(values, x0, y0, x1, y1):
    # values, a raster from north to south whose first and last posts lie
    # at x0, y1 and x1, y0 from the south west corner of the tile, with
    # -32768 for no data, at the posts of the rasters of the DSF,
    # bilinearly, and NaN beyond it or where a post of no data weighs.
    (h, w) = values.shape
    (j, i) = numpy.mgrid[0:posts, 0:posts]
    x = (i / step - x0) / (x1 - x0) * (w - 1)
    y = (y1 - j / step) / (y1 - y0) * (h - 1)
    inside = (x >= 0) & (x <= w - 1) & (y >= 0) & (y <= h - 1)
    out = numpy.full((posts, posts), numpy.nan)
    if not inside.any():
        return out
    (x, y) = (x[inside], y[inside])
    i0 = numpy.minimum(numpy.floor(x).astype(numpy.int64), w - 2)
    j0 = numpy.minimum(numpy.floor(y).astype(numpy.int64), h - 2)
    (fx, fy) = (x - i0, y - j0)
    # Only the window of the raster over the tile turns to floats.
    (a, b) = (i0.min(), j0.min())
    window = values[b : j0.max() + 2, a : i0.max() + 2]
    v = numpy.where(window == -32768, numpy.nan, window.astype(float))
    (i0, j0) = (i0 - a, j0 - b)
    total = numpy.zeros(len(x))
    for (dj, di, weight) in (
        (0, 0, (1 - fx) * (1 - fy)),
        (0, 1, fx * (1 - fy)),
        (1, 0, (1 - fx) * fy),
        (1, 1, fx * fy),
    ):
        total += numpy.where(weight > 0, v[j0 + dj, i0 + di] * weight, 0)
    out[inside] = total
    return out


def sample(r, x, y):
    # r at the points (x, y), in posts, bilinearly, clamped to the grid.
    (h, w) = r.shape
    x = numpy.clip(x, 0, w - 1)
    y = numpy.clip(y, 0, h - 1)
    i = numpy.minimum(numpy.floor(x).astype(numpy.int64), w - 2)
    j = numpy.minimum(numpy.floor(y).astype(numpy.int64), h - 2)
    (fx, fy) = (x - i, y - j)
    r = r.astype(float)
    return (
        r[j, i] * (1 - fx) * (1 - fy)
        + r[j, i + 1] * fx * (1 - fy)
        + r[j + 1, i] * (1 - fx) * fy
        + r[j + 1, i + 1] * fx * fy
    )


def fetch(elevation, sea_level, lon, lat, tile_lat, tile_lon):
    # The fetch of the water at each point, which scales its waves: the
    # depth of the floor over 50 m, between 0 and 1, as Laminar's
    # GetWaterBlend (DSFBuilder.cpp) computes it from the rasters.
    x = (lon - tile_lon) * step
    y = (lat - tile_lat) * step
    depth = sample(elevation, x, y) - sample(sea_level, x, y)
    return numpy.clip(depth / 50, 0, 1)
