#!/usr/bin/env bash
# Rebuild every published geospatial result from pinned inputs, in order.
#
# Inputs (see data/README.md for URLs and checksums):
#   data/external/ems/EMSR044, EMSR045, EMSR046, EMSR517, EMSR629   EMS delineation packages, unpacked
#   data/external/ems/EMSR517_GRA                                    EMS grading packages, unpacked
#   data/external/osm/*.osm.pbf + .poly                              Geofabrik extracts (validation, Larkana)
#   data/external/eu-flood-2013                                      European Flood 2013 (floodline external)
#   data/cvfd                                                        CVFD release
#
# Prague and Dresden read OSM through Overpass (results depend on the live map and OSMnx's cache/); every other
# OSM input is a dated extract. Usage: scripts/reproduce_geo.sh [path to the floodline executable]
set -euo pipefail
FL=${1:-floodline}
EMS=data/external/ems
OSM=data/external/osm

# Satellite layers for the five activations (province scale, res 8) and the agreement with geotagged photos.
for a in EMSR044 EMSR045 EMSR046 EMSR517 EMSR629; do
  "$FL" satellite "$EMS/$a" --out "satellite_$a.csv"
done
"$FL" external
"$FL" agree results/eu_flood_2013_mappable.csv results/satellite_EMSR04{4,5,6}.csv \
  --ems-dirs "$EMS"/EMSR04{4,5,6} --out agreement_euflood

# City responder layers and the map.
city() {  # key, EMS package dir, title, subtitle, [extra args for reach/exposure]
  local key=$1 dir=$2 title=$3 subtitle=$4; shift 4
  "$FL" satellite "$dir" --out "satellite_$key.csv" --resolution 9
  "$FL" reach "results/satellite_$key.csv" --ems "$dir" --out "reach_$key" "$@"
  "$FL" exposure "results/satellite_$key.csv" --ems "$dir" --reach "results/reach_$key.csv" --out "exposure_$key.csv" "$@"
  "$FL" fuse "results/satellite_$key.csv" "results/reach_$key.csv" "results/exposure_$key.csv" \
    --reports results/eu_flood_2013_mappable.csv --out "hexes_$key"
  "$FL" serve "results/hexes_$key.csv" --key "$key" --title "$title" --subtitle "$subtitle" --ems "$dir" \
    --roads "results/reach_${key}_flooded_roads.geojson" --staging "results/reach_${key}_staging.geojson" \
    --photos results/agreement_euflood_distances.csv
}
city prague "$EMS/EMSR045/EMSR045_06PRAHA_DELINEATION_DETAIL01_v2_vector" "Prague, June 2013" \
  "Vltava flood. Copernicus EMS rapid map dated 3 June 2013."
city dresden "$EMS/EMSR044/EMSR044_08DRESDEN_DELINEATION_DETAIL01_v1_vector" "Dresden, June 2013" \
  "Elbe flood. Copernicus EMS rapid map dated 3 June 2013, three days before the crest."
city larkana "$EMS/EMSR629/EMSR629_AOI01_DEL_PRODUCT_r1_RTP01_v2_vector" "Larkana, Sindh, 2022" \
  "Indus floods. Copernicus EMS map from SPOT-6/7 imagery of 30 August 2022." --osm-pbf "$OSM/pakistan-261019.osm.pbf"

# Validation against EMS damage grading, sensitivity, report trust and attacks.
"$FL" validate "$EMS/EMSR517_GRA" --osm-pbf "$OSM/koeln-regbez-261018.osm.pbf" "$OSM/rheinland-pfalz-261018.osm.pbf"
"$FL" sensitivity results/hexes_prague.csv results/hexes_dresden.csv --validation-dir results/validation
"$FL" trust --build-index --evaluate
"$FL" attack results/hexes_prague.csv
echo "done: results/ and docs/ regenerated"
