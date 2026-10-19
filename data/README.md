# Data

`data/cvfd/` is not committed. Download the Close-View Flood Dataset (CVFD) and place it here:

- Kaggle: https://www.kaggle.com/datasets/sunnyshabanali/close-view-flood-dataset-cvfd (CC BY-SA 4.0)
- Expected layout: `images/{train,val,test}/{flooded,non-flooded}/*.jpg` and `masks/{flood,humans,buildings}/...`
- Or point `FLOODLINE_CVFD` at an existing copy.

Provenance (from the dataset card): FloodIMG (CC0) for most flooded scenes, web-scraped non-flood scenes, and
343 self-collected images from Pakistan. All images are 224×224 with EXIF removed; there is no geolocation.

Committed here: `depth_labels.csv`, the hand-labelled 300-image depth benchmark (in progress), and the CVFD
index is regenerated with `floodline index`.
