# Audience

**Primary author:** an astronomer working on the MOJAVE radio jet program
(VLBA monitoring of AGN jets at 15 GHz). They own and run the clustering
pipeline (`cluster_code.py` / `find_clusters.py`) that produces per-source
models of moving jet components.

**Reviewers (the tool's end users):** a small trusted group (~5-30) of
collaborators who today inspect the pipeline output by hand-checking PDFs
and MP4s shared via Google Drive. They are domain experts but the tool
should not assume they have a software background.

**Why this tool exists:** the hand-review process is too limited for close
inspection of clustering solutions. Reviewers need to interactively pan/zoom
plots, switch between models, step through epochs against FITS images, and
leave structured recommendations that the primary author can ingest
mechanically.

**Implication for new work in this repo:** lead with science workflow,
then code structure. Domain terms (core, epoch, component, Tb, EVPA) are
known; modern web / deployment concepts may need more careful explanation.
