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


# --- 0 . module-level variables and constants --- #
SECTOR_MAP = {
    'Tech': ['AAPL', 'MSFT', 'AVGO'],
    'Healthcare': ['UNH', 'JNJ', 'AMGN'],
    'Consumer Staples': ['WMT', 'COST', 'PG'],
    'Energy': ['CVX', 'XOM', 'LNG'],
}





# %%
# --- 1. Helper functions ---- #

def daily_returns(df: pd.DataFrame=None) -> pd.DataFrame:
    """Simple daily percentage returns (first row dropped)."""
    df = self.df_all if df is None else df
    return df.pct_change().dropna()

def log_returns(df: pd.DataFrame=None) -> pd.DataFrame:
    """Daily log returns (first row dropped)."""
    return np.log(df / df.shift(1)).dropna()

def scale_data(df: pd.DataFrame) -> pd.DataFrame:
    """Standardise the data by removing the mean and scaling to unit variance."""
    # firstly we initialise an instance of the StandardScaler class from the sklearn library, 
    # which is a common preprocessing step for PCA,
    scaler = StandardScaler()

    # we call on the fit_transform method of the scaler instance to standardise the returns data, 
    # which means that we subtract the mean and divide by the standard deviation for each column
    #  (stock), so that each column has a mean of 0 and a standard deviation of 1, which is important
    #  for PCA because it is sensitive to the scale of the variables (otherwise may get arbitrarily high
    #  variance explained by the stock with the highest volatility, which may not be desirable)
    scaled_array = scaler.fit_transform(df)

    # make sure the array retains the column and row indices
    scaled_df = pd.DataFrame(scaled_array, columns=df.columns, index=df.index)

    return scaled_df


# %%

# --- 2. PCA and eigenvalue decomposition functions --- #

class PCAResults:
    """
    A class to perform at least one PCA on a given returns dataframe,
    but capable of handling multiple PCA fits with different methods and different numbers of components 
    """
    def __init__(self,
                 price_df: pd.DataFrame,
                 ret_type: str="log",
                 assets: list=None
                 ):
        self.price_df = price_df
        # initialise a returns dataframe in the return type we chose
        if ret_type == "log":
            self.ret_df = log_returns(price_df)
        else:
            self.ret_df = daily_returns(price_df)

        # initialise a scaled returns dataframe by calling the scale_data function defined above
        self.scaled_data = scale_data(self.ret_df)
        
        # if the user did not specify a list of assets, we will use all the columns in the returns
        #  dataframe as the assets for PCA
        self.assets = self.ret_df.columns if assets is None else assets

        # we initialise empty results from pca fitting.
        # note that we may fit multiple pca models with different methods and different numbers
        #  of components, so we will store the results in a dictionary where the keys are the 
        # method and number of components, and the values are the results of the pca fitting, 
        # which is a dataframe with the eigenvalues, explained variance ratio, cumulative variance ratio,
        #  and optionally the eigenvectors/weights for each principal component
        self.pca_results = {}
        self.next_id = 1 # to keep track of the next index for storing results in the pca_results dictionary

    def run_pca(self,  
                n_components: int=None, # in case we wish to limit the number of components
                method = "svd", # in case we want to add more methods in the future, e.g. svd
                store_eigenvectors: bool=True, # whether to store the eigenvectors/weights for each PC in the results dataframe
                return_reconstructed: bool=True, # whether to return the reconstructed returns data using only the first n principal components
                ):
        """
        Run PCA on a returns data, and store the results in a dataframe with the eigenvalues, 
        explained variance ratio, cumulative variance ratio, and optionally the eigenvectors/weights 
        for each principal component.
        """
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
            for i in range(self.scaled_data.shape[1]):
                # if clearly a date column (should just be the index, but just in case), then we skip it
                if self.scaled_data.columns[i].lower() in ["date", "datetime"]:
                    continue
                columns.append(f"weight_{self.scaled_data.columns[i]}")
        
        # initialise this dataframe:
        pca_df = pd.DataFrame(columns=columns)


        # --- 2. PCA and eigenvalue decomposition ---

        # --- 2.1: Method 1 is full eigenvalue decomposition of the correlation matrix, 
        # which is the most common method for PCA, and is also the one that gives us
        #  the most information about the variance explained by each PC, and the 
        # weights of each PC in terms of the original variables (stocks)
        if method == "eigen":

            # calculate correlations

            corr_matrix = self.scaled_data.corr()

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
            eigenvectors = eigenvectors[:, idx].T  # transpose to row-per-eigenvector convention (matches sklearn)

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

        # --- 2.2: Method 2 is to use the PCA class from the sklearn library, which uses singular value decomposition (SVD)
        # we can also do PCA via singular value decomposition (SVD), which is a more general
        #  method that can be used for non-square matrices, and importantly can decompose only into
        #  the first n principal components as opposed to all of them
        #  It is also more numerically stable, but it does not give us the eigenvalues and eigenvectors
        #  directly, so we would need to calculate them from the singular values and singular vectors,

            #
        if method == "svd":
            # --- 2.2.2 fit the PCA model ---
            # we then initialise an instance of the PCA class from the sklearn library,
            # where we can specify the number of principal components we want to keep,
            pca_model = PCA(n_components=n_components)

            # then, we call on the fit method of the PCA instance to fit the model to the scaled data,
            pca_model.fit(self.scaled_data)

            # once this has been fit, the instance of the PCA class, pca_model, now has attributes that give
            #  us the eigenvalues, eigenvectors, explained variance ratio, and cumulative variance ratio, 
            # which we can extract as follows:

            # the eigenvectors are stored in the .components_ attribute of the PCA instance, 
            # with dimensions (n_components, n_features), where each row is an eigenvector, and the rows are ordered
            # in descending order of the eigenvalues, so the first row corresponds to the first (largest) eigenvalue, and so on
            eigenvectors = pca_model.components_

            # conveniently, the PCA class also gives us the explained variance ratio directly in the .explained_variance_ratio_ attribute,
            # which is a 1D array of length n_components, where each element is the proportion of variance explained by the corresponding
            #  principal component, and the elements are ordered in descending order of the eigenvalues
            explained_variance_ratio = pca_model.explained_variance_ratio_

            eigenvalues = pca_model.explained_variance_

            # we can then calculate the cumulative variance ratio by taking the cumulative sum of the explained variance ratio
            cumulative_variance_ratio = np.cumsum(explained_variance_ratio)    


        # optionally, also calculate and return the reconstructed returns data using only the first n principal components
        # in case the below loop doesn't run, we set reconstructed data and residuals as empty
        reconstructed_data = None
        residuals = None
        
        if return_reconstructed:
            if method == "svd":
                # the .fit_transform method of the PCA instance gives us the scores of the principal components,
                #  which are the coordinates of the original data in the new space defined by the principal components,
                #  so we can use these scores to reconstruct the data using only the first n principal components, 
                pc_scores = pca_model.fit_transform(self.scaled_data)

                # revert the transformation so that we get the reconstructed data in the original space of the returns, 
                # but only that part of the returns explained by the first few pcs
                reconstructed_data = pd.DataFrame(
                    pca_model.inverse_transform(pc_scores),
                    index=self.scaled_data.index,
                    columns=self.scaled_data.columns
                )

                # find the unexplained  variation
                residuals = self.scaled_data - reconstructed_data
            
            else:
                print("can't be reconstructed as incompatible fitting method chosen")

    
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
                pca_df[f"weight_{self.scaled_data.columns[i]}"] = eigenvectors[:, i]

        # extend the pca_results dictionary with the results of this pca fitting, where the key is a string that
        #  describes the method and number of components, and the value is a dictionary that contains the pca_df
        #  dataframe, the scaled data, the reconstructed data, and the residuals
        self.pca_results[f"Results_id_{self.next_id}"] = {
            "method": method,
            "n_components": n_components,
            "pca_df": pca_df,
            "scaled_data": self.scaled_data,
            "reconstructed_data": reconstructed_data,
            "residuals": residuals
        }

        # update next id for the next time we run pca and want to store results
        self.next_id += 1

        return pca_df
    
    def create_explained_variation_df(self,
                                    # there are 2 ways that a user can interact with this:
                                    # i is providing a list of already run PCA result ids,
                                    # each of which will correspond do a different spec.
                                    # the other is to pass in a dictionary of specd
                                    result_ids: list = None, 
                                    spec_dict: dict = {"n_components": [1,2,3,4], "methods": ["svd"]},
                                    sector_map: dict=SECTOR_MAP, # for the idiosyncratic variance by sector plot
                                    ) -> pd.DataFrame:

        # first, check if result_ids is provided,
        if result_ids is not None:
            # first, check if there are any results to turn to
            if self.pca_results is None or len(self.pca_results) == 0:
                raise ValueError("No PCA results found. Please run PCA first before creating explained variation dataframe.")
            selected_keys = result_ids
        else:
            if spec_dict is None:
                raise ValueError("No specifications provided. Please provide either a list of result ids or a specification dictionary to run PCA and create explained variation dataframe.")
            # if not, then we run PCA with the provided specifications and get the results
            else:
                # first, check if equal numbers of each parameter are provided. if so, we create a dataframe with 1 row for each
                # specification, and 1 column for each argument
                if len(spec_dict["n_components"]) == len(spec_dict["methods"]):
                    spec_df = pd.DataFrame(spec_dict)
                else:
                    # otherwise, we find every combination of the parameters, and create a dataframe with 1 row for each combination,
                    #  and 1 column for each argument, where the value in each cell is the value of that argument for that combination,
                    #  so if we have n_components = [1,2] and methods = ["svd", "eigen"], then we will have 4 rows in the spec_df,
                    # one for each combination of n_components and method
                    spec_df = pd.DataFrame([(n, m) for n in spec_dict["n_components"] for m in spec_dict["methods"]],
                                            columns=["n_components", "method"])

                # then, we iterate over each row of the spec_df, and for each row, we run PCA with the specified parameters,
                #  and we store the results in the pca_results dictionary with a key that describes the method and number of components, and a value that contains the pca_df dataframe, the scaled data, the reconstructed data, and the residuals
                selected_keys = []
                for index, row in spec_df.iterrows():
                    # note that the below code automatically updates self.pca_results, so we don't need to separately reassign
                    pca_df = self.run_pca(n_components=row["n_components"], method=row["method"], return_reconstructed=True)
                    selected_keys.append(f"Results_id_{self.next_id - 1}")


        # initialise a dataframe ready for plotting
        df_idio = pd.DataFrame({
            'Ticker': self.scaled_data.columns,
            # the next syntax below works in the following way: we iterate over each of the columns
            #  in the returns dataframe, and for each column, we iteratively move from one key to the next
            #  find the key in the sector_map dictionary whose value (which is a list of tickers) contains that column/ticker, and we return that key as the sector for that ticker, so we end up with a list of sectors corresponding to each ticker in the returns dataframe
            'Sector': [next(k for k, v in sector_map.items() if t in v) for t in self.scaled_data.columns]
        })

        # iterate through each of the PCA results, and for each result, we add a new column to the df_idio dataframe with the idiosyncratic variance for each ticker, where the column name is the key of the pca_results dictionary that corresponds to that result, so that we can easily compare the idiosyncratic variance across different PCA specifications
        var_expl_prefix = "explained_variance"
        var_idio_prefix = "idiosyncratic_variance"

        # in the below, we only iterate through the selected keys, which are either the result ids provided by the user, 
        # or the result ids generated by running PCA with the specifications provided by the user, so that we only add 
        # columns for the specifications that the user is interested in comparing in terms of explained and idiosyncratic variance
        for key, result in {k: self.pca_results[k] for k in selected_keys if k in self.pca_results}.items():
            # find the variance explained and unexplained for that specification

            # Calculate Variance that is explained and that unexplained
            # Since original variance was standardized to 1.0, this result is directly the 
            # column-wise variance of each of the reconstructred returns or residuals 
            # (note axis = 0 means rows, because we take the variance within a column across the rows)
            exp_variance = np.var(result["reconstructed_data"], axis=0)
            idio_variance = np.var(result["residuals"], axis=0)

            # update the df_idio dataframe with the new columns for that specification, where the column name is the key of the pca_results dictionary that corresponds to that result, so that we can easily compare the idiosyncratic variance across different PCA specifications, and the value in each cell is the variance explained or unexplained for that ticker in that specification
            column_suffix = f"first_{result['n_components']}_pcs"
            df_idio[var_expl_prefix + "_" + column_suffix] = np.asarray(exp_variance)
            df_idio[var_idio_prefix + "_" + column_suffix] = np.asarray(idio_variance)
           

        return df_idio
        

