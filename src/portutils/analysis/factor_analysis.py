"""
Reusable helper functions for factor decomposition and factor analysis, including 
PCA and eigenvalue decomposition.
"""

# %%
# file paths and reloading
import pathlib

# dataframes and maths
import numpy as np
import pandas as pd

# statistical fitting models and similar
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

# %%
# --- 1. Helper functions ---- #

def daily_returns(df: pd.DataFrame=None) -> pd.DataFrame:
    """Simple daily percentage returns (first row dropped)."""
    df = self.df_all if df is None else df
    return df.pct_change().dropna()

def log_returns(df: pd.DataFrame=None) -> pd.DataFrame:
    """Daily log returns (first row dropped)."""
    return np.log(df / df.shift(1)).dropna()


# %%

# --- 2. PCA and eigenvalue decomposition functions --- #

def pca(ret_df: pd.DataFrame, 
        conv_to_ret: bool=False, 
        ret_type: str="log",
        n_components: int=None, # in case we wish to limit the number of components
        method = "eigen", # in case we want to add more methods in the future, e.g. svd
        store_eigenvectors: bool=False
        ) -> tuple[pd.DataFrame, pd.Series]:
    
    # --- 1. Basic inputs checking and processing ---
    # if we need to convert to returns, do so first, and in the return type we chose
    if conv_to_ret:
        if ret_type == "log":
            ret_df = log_returns(ret_df)
        else:
            ret_df = daily_returns(ret_df)

    # initialise a dataframe to store one row for each eigenvalue/principal component,
    # where the first column is what number PC it is (1st, 2nd, etc), 
    # the next is its eigenvalue, 
    # the next is the proportion of variance explained by that PC,
    # the next column is the cumulative proportion of variance explained by that PC and all the previous ones
    # and, if store_eigenvectors is True, then we also have one column for each of
    # the eigenvector/weights of that PC, aka the weights of the portfolio that corresponds to that PC
    columns = ["PC", "eigenvalue", "explained_variance_ratio", "cumulative_variance_ratio"]
    if store_eigenvectors:
        # we iterate through all of the columns in the returns dataframe except for the time column if exists
        for i in range(ret_df.shape[1]):
            # if clearly a date column (should just be the index, but just in case), then we skip it
            if ret_df.columns[i].lower() in ["date", "datetime"]:
                continue
            columns.append(f"weight_{ret_df.columns[i]}")
    
    # initialise this dataframe:
    pca_df = pd.DataFrame(columns=columns)


    # --- 2. PCA and eigenvalue decomposition ---
    # calculate correlations

    corr_matrix = ret_df.corr()

    # In the below code, we use a numpy function to compute the eigenvalues 
    # and eigenvectors of the correlation matrix. 
    # The eigenvectors are a particular selection of weights among the set of
    # all weights where the sum of the squares of the weights = 1 that also
    # maximises the variance of the portfolio returns.
    # e.g. if the first eigenvector has 0.5 for each of the 4 stocks, then
    # the first principal component is the equally weighted portfolio of all 4 stocks, 
    # no other weights in these assets would lead to a portfolio with higher variance in returns
    # The second eignvector gives us the weights of a portfolio with the second highest variance

    # As for the syntax, np is the numpy library, linalg is the linear algebra module
    # of the numpy library, and eigh is a function that computes the eigenvalues and
    #  eigenvectors of a symmetric matrix (which the correlation matrix is).
    eigenvalues, eigenvectors = np.linalg.eigh(corr_matrix)

    # We then sort the eigenvalues and eigenvectors in descending order of the eigenvalues, 
    # note that the variable eigenvalues is a 1D array of the eigenvalues, 
    # and .argsort() is a method that returns the indices that would sort
    # the array in ascending order, so we use [::-1] to reverse the order to get descending
    #  order ( the :: is a slicing syntax that means "take all elements", 
    # and the -1 means "in reverse order" )
    idx = eigenvalues.argsort()[::-1]
    # of course, now having the indices that would sort the eigenvalues in descending order,
    #  we can use index by these same indices to sort the eigenvectors in the same order,
    #  so that the first column of the eigenvectors matrix corresponds to the first (largest) eigenvalue, and so on
    eigenvalues = eigenvalues[idx]

    # the eignvectors variable is a matrix where each column is an eigenvector, and the 
    # columns are initially ordered in the same order as the eigenvalues were before being sorted
    # so indexing the columns with the same indices that sorted the eigenvalues is the same as
    # sorting the columns/eigenvectors so that the first column corresponds to the first (largest) 
    # eigenvalue, and so on
    eigenvectors = eigenvectors[:, idx]

    # We can then calculate the explained variance ratio for each principal component, 
    # which is the proportion of the total variance in the data that is explained by 
    # each principal component.
    # Note that there can only be as many principal components as there are original variables, 
    # because each PC direction must be orthogonal to the others, so if we have 4 stocks,
    # we can only have 4 orthogonal directions in the 4-dimensional space of stock returns,
    # and the resulting 4 PCs are mutually exclusive and collectively exhaustive in terms of 
    # the variance they explain in the data
    total_var = np.sum(eigenvalues)
    # calculate what proportion of the variance is explained by each eigenvalue/principal component,
    explained_variance_ratio = eigenvalues / total_var
    #  and also the cumulative proportion of variance explained
    cumulative_variance_ratio = np.cumsum(explained_variance_ratio)

    # --- 3. reassign back to dataframe and return ---
    pca_df["PC"] = [f"PC{i+1}" for i in range(len(eigenvalues))]
    pca_df["eigenvalue"] = eigenvalues
    pca_df["explained_variance_ratio"] = explained_variance_ratio
    pca_df["cumulative_variance_ratio"] = cumulative_variance_ratio
    if store_eigenvectors:
        # we iterate over each of the columns (the second dimension of the shape)
        # and for each column, we add a new column to the pca_df with the corresponding
        #  eigenvector/weights
        for i in range(eigenvectors.shape[1]):
            pca_df[f"weight_{ret_df.columns[i]}"] = eigenvectors[:, i]

    return pca_df