"""The device library: run stores of em_only observations, a ``library.yaml`` manifest, and the query layer on top.

``manifest`` reads the strata; ``dataset`` turns a stratum's observations into query rows under one
quantity definition. The model, domain guard and query blocks build on these (T13). ``index`` shows a
stratum's rows by their electrical values at one working frequency (T18.1).
"""
