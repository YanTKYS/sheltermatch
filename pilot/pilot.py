import pandas as pd
from geopy.distance import geodesic

# 住民の座標リストを読み込む（address, latitude, longitude）
residents = pd.read_csv("residents_coordinates.csv")

# 避難所の座標リストを読み込む（name, latitude, longitude）
shelters = pd.read_csv("shelter_coordinates.csv")

# 最寄り避難所を計算する関数
def find_nearest_shelter(resident_lat, resident_lon, shelters):
    if pd.isna(resident_lat) or pd.isna(resident_lon):
        return None
    min_distance = float("inf")
    nearest_shelter = None
    resident_coord = (resident_lat, resident_lon)
    for _, shelter in shelters.iterrows():
        shelter_coord = (shelter["latitude"], shelter["longitude"])
        distance = geodesic(resident_coord, shelter_coord).meters
        if distance < min_distance:
            min_distance = distance
            nearest_shelter = shelter["name"]
    return nearest_shelter

# 最寄り避難所を割り当て
residents["nearest_shelter"] = residents.apply(
    lambda row: find_nearest_shelter(row["latitude"], row["longitude"], shelters), axis=1
)

# 結果をCSVに出力
residents.to_csv("assigned_shelters.csv", index=False)
print("最寄り避難所の割り当てが完了しました。")
