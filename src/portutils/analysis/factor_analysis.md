# Factor Analysis — Concepts

## PCA: `np.linalg.eigh` vs sklearn

`eigh` does raw eigendecomposition on a precomputed correlation matrix — you bring your own matrix, it gives eigenvalues + eigenvectors. Static, one-shot.

Sklearn PCA does the full pipeline: takes raw data → internally computes SVD (not eig) → extracts components, variance explained, scores. More convenient, more numerically stable.

### StandardScaler

Transforms each column to mean=0, std=1. Required before PCA so high-variance assets don't dominate the components. `fit_transform` = compute mean/std from data, then apply the transformation. Equivalent to working on a correlation matrix rather than covariance.

### Why `pca.fit` after instantiation

`PCA(n_components=3)` just creates the object with config — no computation. `.fit(scaled_data)` runs the actual SVD on the data and stores results on the object. Sklearn separates config from computation this way.

### `pca.components_`

Shape: `(n_components, n_features)` → e.g. `(3, 12)` for 12 tickers.

`.T` transposes to `(n_features, n_components)` → `(12, 3)`. This is the **loadings matrix** — each row is a ticker, each column shows how much that ticker contributes to each PC. These are eigenvectors, not PC scores.

PC scores = `pca.transform(scaled_data)` — shape `(n_dates, n_components)`, the actual time series of each principal component.

### sklearn PCA vs `eigh` on corr matrix

sklearn PCA is better for two reasons:
- Uses SVD on raw data — numerically more stable than eigendecomposition on a computed matrix
- Preserves the time dimension — you can extract PC scores (time series), which `eigh` on a static matrix cannot give you

`eigh` is fine for understanding variance structure only. sklearn gives both structure and the temporal dynamics of each factor.

---

## Singular Value Decomposition (SVD)

Factorises any matrix `X` into three matrices:

```
X = U · Σ · Vᵀ
```

- `U` — left singular vectors `(n_samples × n_components)` — directions in observation space
- `Σ` — diagonal matrix of singular values — magnitude of each direction
- `Vᵀ` — right singular vectors `(n_components × n_features)` — directions in feature space → become `pca.components_`

### Why SVD instead of eigendecomposition

Eigendecomposition needs a square symmetric matrix (e.g. covariance matrix `XᵀX`). You compute `XᵀX` first, then decompose it — two steps, and forming `XᵀX` amplifies numerical errors (squaring the condition number).

SVD works directly on `X` — one step, no squaring, more stable. The eigenvalues of `XᵀX` equal `Σ²`, so you get the same answer with better precision.

In plain terms: SVD finds the axes of maximum variance in the data without ever explicitly computing a covariance matrix.
