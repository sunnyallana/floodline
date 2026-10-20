# Data

`data/cvfd/` is not committed. Download the Close-View Flood Dataset (CVFD) and place it here:

- Kaggle: https://www.kaggle.com/datasets/sunnyshabanali/close-view-flood-dataset-cvfd (CC BY-SA 4.0)
- Expected layout: `images/{train,val,test}/{flooded,non-flooded}/*.jpg` and `masks/{flood,humans,buildings}/...`
- Or point `FLOODLINE_CVFD` at an existing copy.

Provenance (from the dataset card): FloodIMG (CC0) for most flooded scenes, web-scraped non-flood scenes, and
343 self-collected images from Pakistan. All images are 224×224 with EXIF removed; there is no geolocation.

Committed here: `depth_labels.csv`, the 300-image depth benchmark (labelled from contact sheets with an AI assistant; human review pending), and the CVFD
index is regenerated with `floodline index`.

## OpenStreetMap extracts used for validation

`floodline validate --osm-pbf` reads these Geofabrik snapshots, not live Overpass, so the result does not
change as OSM is edited. Download them into `data/external/osm/` together with their `.poly` boundaries:

| File | Source | MD5 |
|---|---|---|
| `koeln-regbez-261018.osm.pbf` | https://download.geofabrik.de/europe/germany/nordrhein-westfalen/koeln-regbez-261018.osm.pbf | `04130cd86d5af1eb8611eb93475977fa` |
| `rheinland-pfalz-261018.osm.pbf` | https://download.geofabrik.de/europe/germany/rheinland-pfalz-261018.osm.pbf | `42fca296352315efc956abb12a43473f` |

The `.poly` files are `koeln-regbez.poly` and `rheinland-pfalz.poly` from the same directories, saved as
`<extract name>.poly`. EMS grading packages for EMSR517 come from
`https://cems-mapping-website.s3.eu-west-1.amazonaws.com/static/activations/EMSR517/<product>_vector.zip`.

| `pakistan-261019.osm.pbf` | https://download.geofabrik.de/asia/pakistan-261019.osm.pbf | `e32c1ce2b2640002a44961e5b51d9e78` |

Microsoft Global ML Building Footprints tiles are fetched on demand from the index at
`https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv` and cached under
`data/external/buildings/`; the release date (2026-02-03 for Pakistan) is part of each tile URL.
