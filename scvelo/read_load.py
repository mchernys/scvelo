from . import logging as logg
from .preprocessing.utils import set_initial_size

import os, re
import numpy as np
import pandas as pd
from urllib.request import urlretrieve
from pathlib import Path
from scipy.sparse import issparse
from anndata import AnnData
from scanpy import read, read_loom


def load(filename, backup_url=None, **kwargs):
    numpy_ext = {'npy', 'npz'}
    pandas_ext = {'csv', 'txt', 'tsv'}

    if not os.path.exists(filename) and backup_url is None:
        raise FileNotFoundError('Did not find file {}.'.format(filename))

    elif not os.path.exists(filename):
        d = os.path.dirname(filename)
        if not os.path.exists(d): os.makedirs(d)
        urlretrieve(backup_url, filename)

    ext = Path(filename).suffixes[-1][1:]

    if ext in numpy_ext: return np.load(filename, **kwargs)
    elif ext in pandas_ext: return pd.read_csv(filename, **kwargs)
    else: raise ValueError('"{}" does not end on a valid extension.\n'
                           'Please, provide one of the available extensions.\n{}\n'
                           .format(filename, numpy_ext|pandas_ext))


read_csv = load


def clean_obs_names(data, base='[AGTCBDHKMNRSVWY]', ID_length=12, copy=False):
    """Cleans up the obs_names and identifies sample names.
    For example an obs_name 'samlple1_AGTCdate' is changed to 'AGTC' of the sample 'sample1_date'.
    The sample name is then saved in obs['sample_batch'].
    The genetic codes are identified according to according to https://www.neb.com/tools-and-resources/usage-guidelines/the-genetic-code.

    Arguments
    ---------
    adata: :class:`~anndata.AnnData`
        Annotated data matrix.
    base: `str` (default: `[AGTCBDHKMNRSVWY]`)
        Genetic code letters to be identified.
    ID_length: `int` (default: 12)
        Length of the Genetic Codes in the samples.
    copy: `bool` (default: `False`)
        Return a copy instead of writing to adata.

    Returns
    -------
    Returns or updates `adata` with the attributes
    obs_names: list
        updated names of the observations
    sample_batch: `.obs`
        names of the identified sample batches
    """
    def get_base_list(name, base):
        base_list = base
        while re.search(base_list + base, name) is not None:
            base_list += base
        if len(base_list) == 0:
            raise ValueError('Encountered an invalid ID in obs_names: ', name)
        return base_list

    adata = data.copy() if copy else data

    names = adata.obs_names
    base_list = get_base_list(names[0], base)

    if len(np.unique([len(name) for name in adata.obs_names])) == 1:
        start, end = re.search(base_list, names[0]).span()
        newIDs = [name[start:end] for name in names]
        start, end = 0, len(newIDs[0])
        for i in range(end - ID_length):
            if np.any([ID[i] not in base for ID in newIDs]): start += 1
            if np.any([ID[::-1][i] not in base for ID in newIDs]): end -= 1

        newIDs = [ID[start:end] for ID in newIDs]
        prefixes = [names[i].replace(newIDs[i], '') for i in range(len(names))]
    else:
        prefixes, newIDs = [], []
        for name in names:
            match = re.search(base_list, name)
            newID = re.search(get_base_list(name, base), name).group() if match is None else match.group()
            newIDs.append(newID)
            prefixes.append(name.replace(newID, ''))

    adata.obs_names = newIDs
    if len(prefixes[0]) > 0 and len(np.unique(prefixes)) > 1:
        #idx_names = np.random.choice(len(names), size=20, replace=False)
        #for i in range(len(names[0])):
        #    if np.all([re.search(names[0][:i], names[ix]) for ix in idx_names]) is not None: obs_key = names[0][:i]
        adata.obs['sample_batch'] = pd.Categorical(prefixes) if len(np.unique(prefixes)) < adata.n_obs else prefixes

    adata.obs_names_make_unique()
    return adata if copy else None


def merge(adata, ldata, copy=True):
    """Merges two annotated data matrices.

    Arguments
    ---------
    adata: :class:`~anndata.AnnData`
        Annotated data matrix (reference data set).
    ldata: :class:`~anndata.AnnData`
        Annotated data matrix (to be merged into adata).

    Returns
    -------
    Returns a :class:`~anndata.AnnData` object
    """
    adata.var_names_make_unique()
    ldata.var_names_make_unique()

    if 'spliced' in ldata.layers.keys() and 'initial_size_spliced' not in ldata.obs.keys(): set_initial_size(ldata)
    elif 'spliced' in adata.layers.keys() and 'initial_size_spliced' not in adata.obs.keys(): set_initial_size(adata)

    common_obs = pd.unique(adata.obs_names.intersection(ldata.obs_names))
    common_vars = pd.unique(adata.var_names.intersection(ldata.var_names))

    if len(common_obs) == 0:
        clean_obs_names(adata)
        clean_obs_names(ldata)
        common_obs = adata.obs_names.intersection(ldata.obs_names)

    if copy:
        _adata = adata[common_obs].copy()
        _ldata = ldata[common_obs].copy()
    else:
        adata._inplace_subset_obs(common_obs)
        _adata, _ldata = adata, ldata[common_obs].copy()

    _adata.var_names_make_unique()
    _ldata.var_names_make_unique()

    same_vars = (len(_adata.var_names) == len(_ldata.var_names) and np.all(_adata.var_names == _ldata.var_names))
    join_vars = len(common_vars) > 0

    if join_vars and not same_vars:
        _adata._inplace_subset_var(common_vars)
        _ldata._inplace_subset_var(common_vars)

    for attr in _ldata.obs.keys():
        if attr not in _adata.obs.keys(): _adata.obs[attr] = _ldata.obs[attr]
    for attr in _ldata.obsm.keys():
        if attr not in _adata.obsm.keys(): _adata.obsm[attr] = _ldata.obsm[attr]
    for attr in _ldata.uns.keys():
        if attr not in _adata.uns.keys(): _adata.uns[attr] = _ldata.uns[attr]
    if join_vars:
        for attr in _ldata.layers.keys():
            if attr not in _adata.layers.keys(): _adata.layers[attr] = _ldata.layers[attr]

        if _adata.shape[1] == _ldata.shape[1]:
            same_vars = (len(_adata.var_names) == len(_ldata.var_names) and np.all(_adata.var_names == _ldata.var_names))
            if same_vars:
                for attr in _ldata.var.keys():
                    if attr not in _adata.var.keys(): _adata.var[attr] = _ldata.var[attr]
                for attr in _ldata.varm.keys():
                    if attr not in _adata.varm.keys(): _adata.varm[attr] = _ldata.varm[attr]
            else:
                raise ValueError('Variable names are not identical.')

    return _adata if copy else None


def obs_df(adata, keys, layer=None):
    lookup_keys = [k for k in keys if k in adata.var_names]
    if len(lookup_keys) < len(keys):
        logg.warn(f"Keys {[k for k in keys if k not in adata.var_names]} were not found in `adata.var_names`.")

    df = pd.DataFrame(index=adata.obs_names)
    for l in lookup_keys:
        df[l] = adata.obs_vector(l, layer=layer)
    return df


def var_df(adata, keys, layer=None):
    lookup_keys = [k for k in keys if k in adata.obs_names]
    if len(lookup_keys) < len(keys):
        logg.warn(f"Keys {[k for k in keys if k not in adata.obs_names]} were not found in `adata.obs_names`.")

    df = pd.DataFrame(index=adata.var_names)
    for l in lookup_keys:
        df[l] = adata.var_vector(l, layer=layer)
    return df


def get_df(data, keys=None, layer=None, index=None, columns=None, dropna='all', precision=None):
    """\
    Return values for a specific key in data (from obs, var, obsm, varm, obsp, varp, uns, or layers) as a dataframe.
    Params
    ------
    adata
        AnnData object or a numpy array to get values from.
    keys
        Keys from `.var_names`, `obs_names`, `.var`, `.obs`, `.obsm`, `.varm`, `.obsp`, `.varp`, `.uns`, or `.layers`.
    layer
        Layer of `adata` to use as expression values.
    index
        List to set as index.
    columns
        List to set as columns names.
    dropna
        Whether to drop columns/rows if they display NaNs all over (dropna='all') or in any entry (dropna='any').
    precision
        Set precision for pandas dataframe.
    Returns
    -------
    A dataframe.
    """
    if precision is not None:
        pd.set_option('precision', precision)

    if isinstance(data, AnnData):
        keys = [keys] if isinstance(keys, str) else keys
        key = keys[0]

        s_keys = ['obs', 'var', 'obsm', 'varm', 'uns', 'layers']
        d_keys = [data.obs.keys(), data.var.keys(),
                      data.obsm.keys(), data.varm.keys(),
                      data.uns.keys(), data.layers.keys()]

        if hasattr(data, 'obsp') and hasattr(data, 'varp'):
            s_keys.extend(['obsp', 'varp'])
            d_keys.extend([data.obsp.keys(), data.varp.keys()])

        if keys is None:
            df = data.to_df()
        elif key in data.var_names:
            df = obs_df(data, keys, layer=layer)
        elif key in data.obs_names:
            df = var_df(data, keys, layer=layer)
        else:
            s_key = [s for (s, d_key) in zip(s_keys, d_keys) if key in d_key]
            if len(s_key) == 0:
                raise ValueError("'" + key + "' not found in any of " + ", ".join(s_keys) + ".")
            if len(s_key) > 1:
                logg.warn("'" + key + "' found multiple times in " + ", ".join(s_key) + ".")

            s_key = s_key[-1]
            df = eval('data.' + s_key)[keys if len(keys) > 1 else key]

            if index is None:
                index = data.var_names if s_key == 'varm' else data.obs_names if s_key == 'obsm' else None
    else:
        df = data

    if issparse(df):
        df = np.array(df.A)
    if columns is None and hasattr(df, 'names'):
        columns = df.names

    df = pd.DataFrame(df, index=index, columns=columns)

    if dropna:
        df.replace("", np.nan, inplace=True)
        df.dropna(how=dropna if not isinstance(dropna, str) else 'all', inplace=True)

    return df
