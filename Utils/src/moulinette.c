/*
 * moulinette: reorders the triangles and vertices of an Ortho4XP mesh
 * so that X-Plane draws it with fewer cache misses.
 *
 * usage: moulinette zoomlevel sourcefile.mesh [destfile.mesh]
 *
 * It exits with status 1 when it cannot read or write a mesh, and leaves
 * the mesh as it was: it writes the new mesh beside the old, then renames
 * it over.
 *
 * Pascal (Scratchfly @x-plane.org) wrote moulinette in C++, and Ortho4XP
 * shipped it as a binary. This file restates his program in C from that
 * binary, for the same use and under the same terms. The algorithm, the
 * calls to rand, and the messages are his, and the functions keep the
 * names of his.
 *
 * The triangles go in clusters, one per texture of the zoomlevel (all in
 * one cluster when the zoomlevel is 0). In each cluster, a walk from a
 * random triangle crosses to an adjacent triangle of the same type when
 * it can, preferring the edges whose vertices the fewest pending
 * triangles share, and starts a new strip otherwise. The vertices then
 * follow the order in which the triangles first use them, and those no
 * triangle uses go.
 */
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#include <windows.h>
#endif

enum {
	Linelen = 256,	/* longest line kept, newline included */
	Maxcount = INT_MAX/4	/* most items in a section, for int indices */
};

/* M_PI, which strict C lacks */
static const double Pi = 3.14159265358979323846;

typedef struct AvlNode AvlNode;

/* a node of the AVL tree that maps an edge to its half-edges */
struct AvlNode {
	AvlNode	*left;
	AvlNode	*right;
	int	height;	/* longest path down, or -1 when stale */
	int	value;	/* last half-edge on the edge, or -1 */
	int	key;	/* the edge, from edgekey */
};

static void *emalloc(size_t);
static int replace(char*, char*);
static int toolong(char*, FILE*);
static char *voidlines(int);
static int edgekey(int, int);
static int avlheight(AvlNode*);
static int avlbalance(AvlNode*);
static int *avlsearch(AvlNode**, int, char*);
static void avldelete(AvlNode**);
static void order(int*);

int
main(int argc, char *argv[])
{
	char header[Linelen], line[Linelen], word[Linelen];
	char *vert, *norm, *newvert, *newnorm, *outvert, *dst, *tmp;
	int *tri, *newtri, *group, *valence, *info, *next, *stack, *pending, *map, *p, *q;
	double *coord, x, y, z, lat, scale;
	int nv, nn, nt, iv, in, it, state, zl, i, j, k, t, t2, h, a, b, c, d, v, n;
	int found, tx, ty, xmin, xmax, ymin, ymax, nx, ny, count, best, nout;
	int nstack, npending, type, s[3], chains, strips, shared;
	char insert;
	AvlNode *tree;
	FILE *f;

	puts("#### Mesh File Optimizer ####");
	if (argc < 3 || argc > 4) {
		puts("* Groups triangles per zoomlevel tile and per type.");
		puts("* Reorders the triangle list so as successive triangles are adjacent if possible.");
		puts("* Reorders the vertex list to the referring order of the triangle list.");
		puts("* Removes unreferenced vertices.");
		puts("Usage: moulinette zoomlevel sourcefile.mesh [destfile.mesh]");
		return 2;
	}
	srand((unsigned)time(NULL));

	nv = nn = nt = 0;
	vert = norm = NULL;
	tri = NULL;
	f = fopen(argv[2], "r");
	if (f == NULL) {
		puts("  Error: Unable to read mesh file.");
		return 1;
	}
	printf("  Reading mesh file %s ...\n", argv[2]);
	if (fgets(header, sizeof header, f) == NULL) {
		puts("  Error: Mesh file is empty.");
		return 1;
	}
	if (toolong(header, f)) {
		puts("  Error: Mesh file has a line too long.");
		return 1;
	}
	state = 0;
	iv = in = it = 0;
	while (fgets(line, sizeof line, f) != NULL) {
		if (toolong(line, f)) {
			puts("  Error: Mesh file has a line too long.");
			return 1;
		}
		switch (state) {
		case 0:
			if (sscanf(line, "%s", word) == 1) {
				if (strcmp(word, "Vertices") == 0)
					state = 2;
				else if (strcmp(word, "Normals") == 0)
					state = 4;
				else if (strcmp(word, "Triangles") == 0)
					state = 6;
			}
			break;
		case 2:
			n = sscanf(line, "%d", &nv);
			state = 0;
			if (n == 1 && nv > Maxcount) {
				puts("  Error: Mesh file is too large.");
				return 1;
			}
			if (n == 1) {
				iv = 0;
				if (nv > 0) {
					free(vert);
					vert = voidlines(nv);
					state = 3;
				}
			}
			break;
		case 3:
			if (sscanf(line, "%lf %lf %lf", &x, &y, &z) != 3) {
				puts("  Error: Mesh file has a malformed vertex.");
				return 1;
			}
			strncpy(vert + (size_t)iv*Linelen, line, Linelen);
			if (++iv >= nv)
				state = 0;
			break;
		case 4:
			n = sscanf(line, "%d", &nn);
			state = 0;
			if (n == 1 && nn > Maxcount) {
				puts("  Error: Mesh file is too large.");
				return 1;
			}
			if (n == 1) {
				in = 0;
				if (nn > 0) {
					free(norm);
					norm = voidlines(nn);
					state = 5;
				}
			}
			break;
		case 5:
			if (sscanf(line, "%lf %lf", &x, &y) != 2) {
				puts("  Error: Mesh file has a malformed normal.");
				return 1;
			}
			strncpy(norm + (size_t)in*Linelen, line, Linelen);
			if (++in >= nn)
				state = 0;
			break;
		case 6:
			n = sscanf(line, "%d", &nt);
			state = 0;
			if (n == 1 && nt > Maxcount) {
				puts("  Error: Mesh file is too large.");
				return 1;
			}
			if (n == 1) {
				it = 0;
				if (nt > 0) {
					free(tri);
					tri = emalloc((size_t)nt * 4 * sizeof tri[0]);
					for (i = 0; i < nt*4; i++)
						tri[i] = -1;
					state = 7;
				}
			}
			break;
		case 7:
			a = b = c = d = 0;
			if (sscanf(line, "%d %d %d %d", &a, &b, &c, &d) == 4) {
				if (tri != NULL) {
					tri[4*it] = a > 0 ? a - 1 : -1;
					tri[4*it + 1] = b > 0 ? b - 1 : -1;
					tri[4*it + 2] = c > 0 ? c - 1 : -1;
					tri[4*it + 3] = d;
				}
				if (++it >= nt)
					state = 0;
			} else
				state = 0;
			break;
		}
	}
	n = ferror(f);
	fclose(f);
	if (n != 0) {
		puts("  Error: Unable to read mesh file.");
		return 1;
	}
	printf("    Read %d vertices, %d normals, %d triangles.\n", nv, nn, nt);
	if (tri == NULL) {
		puts("  Error: Unable to process triangles.");
		return 1;
	}
	if (vert == NULL) {
		puts("  Error: Unable to process vertices.");
		return 1;
	}
	if (iv < nv || in < nn || it < nt) {
		puts("  Error: Mesh file is incomplete.");
		return 1;
	}
	for (i = 0; i < nt*4; i++)
		if (i%4 != 3 && (tri[i] < 0 || tri[i] >= nv)) {
			puts("  Error: A triangle refers to a missing vertex.");
			return 1;
		}

	newtri = emalloc((size_t)nt * 4 * sizeof newtri[0]);
	for (i = 0; i < nt; i++) {
		newtri[4*i] = newtri[4*i + 1] = newtri[4*i + 2] = -1;
		newtri[4*i + 3] = 0;
	}
	group = emalloc((size_t)nt * sizeof group[0]);
	for (i = 0; i < nt; i++)
		group[i] = -1;
	valence = NULL;
	if (nv >= 1) {
		valence = emalloc((size_t)nv * sizeof valence[0]);
		for (i = 0; i < nv; i++)
			valence[i] = 0;
	}

	/* group[t] < 0 holds -1 or the cluster of t minus the clusters, minus 1 */
	zl = 0;
	sscanf(argv[1], "%d", &zl);
	if (zl > 0) {
		coord = emalloc((size_t)nv * 2 * sizeof coord[0]);
		for (i = 0; i < nv; i++) {
			x = y = z = 0.0;
			v = 0;
			sscanf(vert + (size_t)i*Linelen, "%lf %lf %lf %d", &x, &y, &z, &v);
			coord[2*i] = x;
			coord[2*i + 1] = y;
		}
		info = emalloc((size_t)nt * 3 * sizeof info[0]);
		found = 0;
		xmin = xmax = ymin = ymax = -1;
		for (t = 0; t < nt; t++) {
			a = tri[4*t];
			b = tri[4*t + 1];
			c = tri[4*t + 2];
			info[3*t] = info[3*t + 1] = info[3*t + 2] = 0;
			if (a < 0 || b < 0 || c < 0 || a >= nv || b >= nv || c >= nv)
				continue;
			lat = (coord[2*a + 1] + coord[2*b + 1] + coord[2*c + 1]) / 3.0;
			if (!(lat > -90.0) || !(lat < 90.0))
				continue;
			/* the texture of zoomlevel zl, 16 tiles wide, under the centroid */
			scale = pow(2.0, zl - 5);
			tx = (int)(((coord[2*a] + coord[2*b] + coord[2*c]) / 3.0 / 180.0 + 1.0) * scale);
			ty = (int)(scale * (1.0 - log(tan((90.0 + lat) * (Pi / 360))) / Pi));
			if (!found) {
				xmin = xmax = tx;
				ymin = ymax = ty;
				found = 1;
			} else {
				if (tx < xmin)
					xmin = tx;
				if (ty < ymin)
					ymin = ty;
				if (tx > xmax)
					xmax = tx;
				if (ty > ymax)
					ymax = ty;
			}
			info[3*t] = tx;
			info[3*t + 1] = ty;
			info[3*t + 2] = 1;
		}
		if (found) {
			nx = xmax + 1 - xmin;
			ny = ymax + 1 - ymin;
			n = nx * ny;
			printf("  Found %d x %d = %d clusters for level %d.\n", nx, ny, n, zl);
			for (t = 0; t < nt; t++)
				if (info[3*t + 2] > 0 && group[t] < 0)
					group[t] += (info[3*t + 1] - ymin)*nx + (info[3*t] - xmin) - n;
		}
		free(info);
		free(coord);
	}

	/*
	 * Half-edge 3t+k of triangle t runs between its vertices k+1 and k+2,
	 * modulo 3. next chains the half-edges with the same key, from the
	 * tree, and group[t] becomes the place of t in newtri once out.
	 */
	next = emalloc((size_t)nt * 3 * sizeof next[0]);
	nout = 0;
	for (;;) {
		best = 0;
		for (i = 0; i < nt; i++)
			if (group[i] < best)
				best = group[i];
		if (best >= 0)
			break;
		count = 0;
		for (i = 0; i < nt; i++)
			if (group[i] == best)
				count++;
		printf("    Reordering %d triangles in cluster %d...\n", count, ~best);

		tree = NULL;
		for (t = 0; t < nt; t++) {
			if (group[t] != best)
				continue;
			a = tri[4*t];
			b = tri[4*t + 1];
			c = tri[4*t + 2];
			h = 3*t;
			if (a >= 0 && valence != NULL && a < nv)
				valence[a]++;
			if (b >= 0 && c >= 0) {
				insert = 1;
				p = avlsearch(&tree, edgekey(b, c), &insert);
				if (p != NULL) {
					next[h] = *p;
					*p = h;
				}
			}
			if (valence != NULL && b >= 0 && b < nv)
				valence[b]++;
			if (c >= 0 && a >= 0) {
				insert = 1;
				p = avlsearch(&tree, edgekey(c, a), &insert);
				if (p != NULL) {
					next[h + 1] = *p;
					*p = h + 1;
				}
			}
			if (valence != NULL && c >= 0 && c < nv)
				valence[c]++;
			if (a >= 0 && b >= 0) {
				insert = 1;
				p = avlsearch(&tree, edgekey(a, b), &insert);
				if (p != NULL) {
					next[h + 2] = *p;
					*p = h + 2;
				}
			}
		}

		/*
		 * half-edges to cross: stack for the same type, pending for others.
		 * In a manifold mesh, a half-edge has at most one opposite, so each
		 * holds at most 3 per triangle.
		 */
		stack = emalloc((size_t)nt * 3 * sizeof stack[0]);
		pending = emalloc((size_t)nt * 3 * sizeof pending[0]);
		nstack = npending = 0;
		for (;;) {
			if (nstack == 0) {
				if (npending < 1) {
					k = 0;
					for (i = 0; i < nt; i++)
						if (group[i] == best) {
							k++;
							if (rand()%k < 1) {
								nstack = 1;
								stack[0] = 3*i + rand()%3;
							}
						}
				} else {
					type = tri[4*(pending[npending - 1] / 3) + 3];
					for (i = 0; i < npending; i++) {
						h = pending[i];
						if (tri[4*(h / 3) + 3] != type)
							pending[i - nstack] = h;
						else
							stack[nstack++] = h;
					}
					npending -= nstack;
				}
				if (nstack == 0)
					break;
			}
			h = stack[--nstack];
			t = h / 3;
			if (group[t] >= 0)
				continue;
			k = h - 3*t;
			p = tri + 4*t;
			q = newtri + 4*nout;
			q[0] = p[(k + 1)%3];
			q[1] = p[(k + 2)%3];
			q[2] = p[(k + 3)%3];
			if (valence != NULL)
				for (j = 0; j < 3; j++)
					if (q[j] >= 0 && q[j] < nv)
						valence[q[j]]--;
			q[3] = p[3];
			group[t] = nout++;

			/* the remaining triangles on the vertices of each edge */
			s[0] = s[1] = s[2] = 0;
			if (valence != NULL) {
				a = p[0];
				b = p[1];
				c = p[2];
				if (b >= 0 && b < nv)
					s[0] = valence[b];
				if (c >= 0 && c < nv) {
					s[0] += valence[c];
					s[1] = valence[c];
				}
				if (a >= 0 && a < nv) {
					s[2] = valence[a];
					s[1] += s[2];
				}
				if (b >= 0 && b < nv)
					s[2] += valence[b];
			}
			order(s);
			for (j = 0; j < 3; j++) {
				a = p[(s[j] + 1)%3];
				b = p[(s[j] + 2)%3];
				if (a < 0 || b < 0)
					continue;
				/* the half-edges the other way, in the triangles still to go */
				q = avlsearch(&tree, edgekey(b, a), NULL);
				if (q == NULL)
					continue;
				for (h = *q; h >= 0; h = next[h]) {
					t2 = h / 3;
					if (group[t2] != best)
						continue;
					k = h - 3*t2;
					if (tri[4*t2 + (k + 2)%3] != a || tri[4*t2 + (k + 1)%3] != b)
						continue;
					if (nstack == 3*nt || npending == 3*nt) {
						puts("  Error: Too many triangles share an edge.");
						return 1;
					}
					if (tri[4*t2 + 3] == p[3])
						stack[nstack++] = h;
					else
						pending[npending++] = h;
				}
			}
		}
		free(stack);
		free(pending);
		avldelete(&tree);
	}
	free(next);
	free(valence);
	free(group);
	free(tri);

	/* a chain breaks between triangles without a common vertex, a strip without a common edge */
	chains = strips = 1;
	for (i = 0; i < nt - 1; i++) {
		p = newtri + 4*i;
		q = p + 4;
		shared = 0;
		for (j = 0; j < 3; j++)
			for (k = 0; k < 3; k++)
				shared += p[j] == q[k];
		if (shared < 2) {
			strips++;
			if (shared == 0)
				chains++;
		}
	}
	printf("  Average chain length: %d, strip length: %d.\n",
		(int)((double)nt / chains + 0.5), (int)((double)nt / strips + 0.5));

	if (norm == NULL) {
		newvert = voidlines(nv);
		newnorm = NULL;
	} else if (nv != nn) {
		printf("  Warning: Vertex count %d does not match Normal %d count. Won't be reordered.\n", nv, nn);
		outvert = vert;
		goto Write;
	} else {
		newvert = voidlines(nv);
		newnorm = voidlines(nn);
	}
	puts("  Reordering vertices...");
	map = emalloc((size_t)nv * sizeof map[0]);
	for (i = 0; i < nv; i++)
		map[i] = -1;
	count = 0;
	for (i = 0; i < nt*4; i++) {
		if (i%4 == 3)
			continue;
		v = newtri[i];
		if (v < 0 || v >= nv)
			continue;
		if (map[v] < 0) {
			map[v] = count;
			strncpy(newvert + (size_t)count*Linelen, vert + (size_t)v*Linelen, Linelen);
			if (newnorm != NULL)
				strncpy(newnorm + (size_t)count*Linelen, norm + (size_t)v*Linelen, Linelen);
			count++;
		}
		newtri[i] = map[v];
	}
	printf("    Referenced vertices: %d out of %d.\n", count, nv);
	nv = nn = count;
	free(map);
	free(vert);
	if (newnorm != NULL) {
		free(norm);
		norm = newnorm;
	}
	outvert = newvert;

Write:
	/* the mesh goes to dst.tmp, which replaces dst once whole */
	dst = argc < 4 ? argv[2] : argv[3];
	tmp = emalloc(strlen(dst) + sizeof ".tmp");
	strcpy(tmp, dst);
	strcat(tmp, ".tmp");
	f = fopen(tmp, "w");
	if (f == NULL) {
		puts("  Error: Unable to write mesh file.");
		return 1;
	}
	printf("  Writing mesh file %s ...\n", dst);
	fputs(header, f);
	fputs("Dimension 3\n", f);
	fprintf(f, "\nVertices\n%d\n", nv);
	for (i = 0; i < nv; i++)
		fputs(outvert + (size_t)i*Linelen, f);
	if (norm != NULL) {
		fprintf(f, "\nNormals\n%d\n", nn);
		for (i = 0; i < nn; i++)
			fputs(norm + (size_t)i*Linelen, f);
	}
	fprintf(f, "\nTriangles\n%d\n", nt);
	for (i = 0; i < nt; i++)
		fprintf(f, "%d %d %d %d\n", newtri[4*i] + 1, newtri[4*i + 1] + 1,
			newtri[4*i + 2] + 1, newtri[4*i + 3]);
	n = ferror(f);
	if (fclose(f) != 0 || n != 0) {
		remove(tmp);
		puts("  Error: Unable to write mesh file.");
		return 1;
	}
	if (replace(tmp, dst) != 0) {
		printf("  Error: Unable to rename %s to %s.\n", tmp, dst);
		return 1;
	}
	printf("    Write %d vertices, %d normals, %d triangles.\n", nv, nn, nt);
	free(newtri);
	free(outvert);
	free(norm);
	free(tmp);
	puts("Finished.");
	return 0;
}

/* renames file a to b, over a file b, which rename refuses on Windows */
static int
replace(char *a, char *b)
{
#ifdef _WIN32
	return MoveFileExA(a, b, MOVEFILE_REPLACE_EXISTING) ? 0 : -1;
#else
	return rename(a, b);
#endif
}

/* whether fgets cut line short of its newline before the end of f */
static int
toolong(char *line, FILE *f)
{
	return strchr(line, '\n') == NULL && !feof(f);
}

static void *
emalloc(size_t n)
{
	void *p;

	p = malloc(n > 0 ? n : 1);
	if (p == NULL) {
		fputs("moulinette: out of memory\n", stderr);
		exit(1);
	}
	return p;
}

/* n lines of Linelen bytes, each holding "Void\n" */
static char *
voidlines(int n)
{
	char *s;
	int i;

	s = emalloc((size_t)n * Linelen);
	for (i = 0; i < n; i++)
		strncpy(s + (size_t)i*Linelen, "Void\n", Linelen);
	return s;
}

/* the edge from vertex a to vertex b, with a in the low 16 bits */
static int
edgekey(int a, int b)
{
	return (int)((unsigned)(a & 0xffff) + (unsigned)b*0x10000u);
}

static int
avlheight(AvlNode *n)
{
	int l, r;

	if (n->height >= 0)
		return n->height;
	l = n->left != NULL ? avlheight(n->left) + 1 : 0;
	r = n->right != NULL ? avlheight(n->right) + 1 : 0;
	n->height = l > r ? l : r;
	return n->height;
}

static int
avlbalance(AvlNode *n)
{
	int l, r;

	l = n->left != NULL ? avlheight(n->left) + 1 : 0;
	r = n->right != NULL ? avlheight(n->right) + 1 : 0;
	return l - r;
}

/*
 * Returns the value of key in the tree at *root, or NULL. When *insert is
 * set, a missing key gets a node with value -1, and *insert stays set
 * when it was missing.
 */
static int *
avlsearch(AvlNode **root, int key, char *insert)
{
	AvlNode *n, *m, *top;
	int *value, bal;

	if (root == NULL)
		return NULL;
	n = *root;
	if (n != NULL && n->key == key) {
		if (insert != NULL)
			*insert = 0;
		return &n->value;
	}
	value = NULL;
	if (n != NULL)
		value = avlsearch(n->key < key ? &n->right : &n->left, key, insert);
	if (value == NULL) {
		if (insert == NULL || !*insert)
			return NULL;
		n = emalloc(sizeof *n);
		n->left = n->right = NULL;
		n->height = -1;
		n->value = -1;
		n->key = key;
		*root = n;
		return &n->value;
	}
	if (insert == NULL || !*insert)
		return value;
	top = n;
	n->height = -1;
	bal = avlbalance(n);
	if (bal >= 2) {
		if (avlbalance(n->left) < 0) {
			m = n->left;
			top = m->right;
			n->left = top->right;
			m->right = top->left;
			top->right = n;
			top->left = m;
			m->height = -1;
		} else {
			top = n->left;
			n->left = top->right;
			top->right = n;
		}
		n->height = -1;
	} else if (bal < -1) {
		if (avlbalance(n->right) < 1) {
			top = n->right;
			n->right = top->left;
			top->left = n;
		} else {
			m = n->right;
			top = m->left;
			n->right = top->left;
			m->left = top->right;
			top->left = n;
			top->right = m;
			m->height = -1;
		}
		n->height = -1;
	}
	*root = top;
	return value;
}

static void
avldelete(AvlNode **root)
{
	if (root == NULL || *root == NULL)
		return;
	avldelete(&(*root)->left);
	avldelete(&(*root)->right);
	free(*root);
	*root = NULL;
}

/*
 * Replaces the three edge scores in s by the order in which to push the
 * edges, picking at random among the orders the scores allow.
 */
static void
order(int *s)
{
	int a, b, c, mask, n, pick;

	a = s[0];
	b = s[1];
	c = s[2];
	mask = a <= b ? 0x3f : 0x13;
	if (a > c)
		mask = a <= b ? 7 : 3;
	if (a < b)
		mask &= 0x2c;
	if (b > c)
		mask &= 0x0d;
	if (a < c)
		mask &= 0x38;
	if (b < c)
		mask &= 0x32;
	pick = -1;
	n = 0;
	if (mask & 1) {
		rand();
		n++;
		pick = 0;
	}
	if (mask & 2) {
		n++;
		if (rand()%n < 1)
			pick = 1;
	}
	if (mask & 4) {
		n++;
		if (rand()%n < 1)
			pick = 2;
	}
	if (mask & 8) {
		n++;
		if (rand()%n < 1)
			pick = 3;
	}
	if (mask & 0x10) {
		n++;
		if (rand()%n < 1)
			pick = 4;
	}
	if ((mask & 0x20) && rand()%(n + 1) < 1)
		pick = 5;
	switch (pick) {
	case 0:
		s[0] = 0, s[1] = 1, s[2] = 2;
		break;
	case 1:
		s[0] = 0, s[1] = 2, s[2] = 1;
		break;
	case 2:
		s[0] = 1, s[1] = 0, s[2] = 2;
		break;
	case 3:
		s[0] = 1, s[1] = 2, s[2] = 0;
		break;
	case 4:
		s[0] = 2, s[1] = 0, s[2] = 1;
		break;
	case 5:
		s[0] = 2, s[1] = 1, s[2] = 0;
		break;
	default:
		s[0] = s[1] = s[2] = -1;
		break;
	}
}
