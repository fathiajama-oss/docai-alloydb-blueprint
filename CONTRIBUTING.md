# How to Contribute

Contributions to this reference implementation are welcome!

## Contributor License Agreement

Contributions to this project must be accompanied by a Contributor License Agreement (CLA). You (or your employer) retain the copyright to your contribution; this simply gives permission to use and redistribute your contributions as part of the project.

Visit <https://cla.developers.google.com/> to view your current agreements on file or to sign a new one.

## Community Guidelines & Pull Requests

1. Fork the repository and create a feature branch from `main`.
2. Ensure all source files retain their `SPDX-License-Identifier: Apache-2.0` headers.
3. Do not commit `.env` files, credentials, real GCP project IDs, or real personal identity documents (`sample_docs/` must only contain synthetic specimen documents).
4. Run the automated test suite before opening a Pull Request:

```bash
pytest -v
```
