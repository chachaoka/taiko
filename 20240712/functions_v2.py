import os
import pandas as pd
import warnings
import numpy as np
import pandas as pd
#%matplotlib inline#Jupyter Notebook 専用のマジックコマンド
import matplotlib.pyplot as plt
import re
import time
import shutil
import shap
import locale
import seaborn as sns
import matplotlib as mpl
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from dateutil.relativedelta import relativedelta
from IPython.display import display, clear_output
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_squared_error
from sklearn.model_selection import train_test_split
from matplotlib.gridspec import GridSpec
from datetime import datetime
from datetime import timedelta
from PIL import Image
from sklearn.linear_model import Ridge
from sklearn.model_selection import train_test_split
from sklearn.linear_model import Lasso
from sklearn.metrics import mean_squared_error, max_error, mean_absolute_error
from datetime import datetime, time as dt_time
import sys

# 非稼動日の時間を削除する関数
# 土曜日9時から月曜7時までの時間を除外
# is_excluded_time関数がFalseを返す日時だけを残します
def is_excluded_time(timestamp):
    #土曜日（weekday() == 5）の9時以降であれば、True を返す
    if timestamp.weekday() == 5 and timestamp.hour >= 9:
        return True
    #日曜日（weekday() == 6）であれば、True を返す。
    elif timestamp.weekday() == 6:
        return True
    #月曜日（weekday() == 0）の8時未満であれば、True を返す
    elif timestamp.weekday() == 0 and timestamp.hour < 8:
        return True
    else:
        return False
    

def calculate_hourly_counts(df, part_number, time_col, start_date, end_date):
    
    """
    指定された品番のイベント時間（1時間）ごとのかんばん数、納入便、平均検収時間を計算する関数
    
    Parameters:
    df (pd.DataFrame): データフレーム
    part_number (str): 対象品番
    time_col (str): イベント時間を表す列名
    start_date (str): 開始日付（YYYY-MM-DD形式）
    end_date (str): 終了日付（YYYY-MM-DD形式）
    
    Returns:
    pd.DataFrame: 1時間ごとのかんばん数と納入便および平均検収時間
    """
    
    # 品番でフィルタリング
    filtered_df = df[df['品番'] == part_number].copy()
    
    # イベント時間を1時間単位表記にする
    filtered_df['イベント時間'] = pd.to_datetime(filtered_df[time_col], errors='coerce').dt.floor('H')
    
    # 指定された期間を1時間毎に計算
    date_range = pd.date_range(start=start_date, end=end_date, freq='H')

    # 除外された時間をフィルタリング
    date_range = [dt for dt in date_range if not is_excluded_time(dt)]
    filtered_df = filtered_df[filtered_df['イベント時間'].isin(date_range)]

    print(len(date_range))

    #非稼動日時間を削除済み
    hourly_counts = filtered_df.groupby('イベント時間').size().reindex(date_range, fill_value=0)
    
    delivery_totals = []
    reception_times = []
    if time_col == '検収日時':
        delivery_col = '納入便'
        # 納入便をint型に変換
        filtered_df[delivery_col] = filtered_df[delivery_col].astype(int)
        # イベント時間ごとの納入便の合計を計算
        delivery_totals = filtered_df.groupby('イベント時間')[delivery_col].sum().reindex(date_range, fill_value=0)
        # 結果データフレームを作成して納入便の合計を追加
        delivery_totals = delivery_totals / hourly_counts # 納入便数の計算
        delivery_totals = delivery_totals.fillna(0)  # NaNを0に置き換え
        delivery_totals = delivery_totals.astype(int) # int型に変換
        # イベント時間ごとの平均検収時間を計算
        reception_times = filtered_df.groupby('イベント時間')[time_col].apply(lambda x: pd.to_datetime(x.dt.time.astype(str)).mean().time()).reindex(date_range)
        reception_times = reception_times.fillna('00:00:00')  # NaNを00:00:00に置き換え
    
    return hourly_counts, delivery_totals, reception_times

def calculate_business_time_base(row, order_datetime, warehouse_datetime):
    
    # 発注日時または順立装置入庫日時がNaTの場合、NaNを返す
    if pd.isna(order_datetime) or pd.isna(warehouse_datetime):
        return np.nan

    # 全体の時間差を計算
    total_time = warehouse_datetime - order_datetime
    current_datetime = order_datetime

    # 本当は、非稼動日時間に対してやるべきだが、、中央値というマクロな量を見ているのと、休日出勤は稀なのでこれでOK
    # 発注日時から順立装置入庫日時までの期間を1時間ごとに進める
    while current_datetime < warehouse_datetime:
        # 土曜の9時から月曜の7時までの間の時間を除去
        if current_datetime.weekday() == 5 and current_datetime.hour >= 9:  # 土曜日の9時以降
            next_monday = current_datetime + pd.Timedelta(days=2)
            next_monday = next_monday.replace(hour=7, minute=0, second=0)
            if next_monday < warehouse_datetime:
                total_time -= next_monday - current_datetime
                current_datetime = next_monday
            else:
                total_time -= warehouse_datetime - current_datetime
                break
        else:
            # 1時間進める
            current_datetime += pd.Timedelta(hours=1)
            if current_datetime.weekday() == 5 and current_datetime.hour == 9:
                # 進めた結果が土曜日の9時に到達した場合
                next_monday = current_datetime + pd.Timedelta(days=2)
                next_monday = next_monday.replace(hour=7, minute=0, second=0)
                if next_monday < warehouse_datetime:
                    total_time -= next_monday - current_datetime
                    current_datetime = next_monday
                else:
                    total_time -= warehouse_datetime - current_datetime
                    break

    # 日数として計算し、小数点形式に変換
    return total_time.total_seconds() / (24 * 3600)

# ビジネスタイムの差分を計算する関数
def calculate_business_time_order(row):
    
    order_datetime = row['発注日時']
    warehouse_datetime = row['順立装置入庫日時']

    # 日数として計算し、小数点形式に変換
    return calculate_business_time_base(row, order_datetime, warehouse_datetime)

def calculate_business_time_reception(row):
    
    order_datetime = row['検収日時']
    warehouse_datetime = row['順立装置入庫日時']

    # 日数として計算し、小数点形式に変換
    return calculate_business_time_base(row, order_datetime, warehouse_datetime)

def calculate_median_lt(product_code,df):
    
    """
    指定された品番の発注〜順立装置入庫LTの中央値を計算する関数。

    Parameters:
    product_code (str): 品番

    Returns:
    float: 発注〜順立装置入庫LTの中央値
    """
    
    # 指定された品番のデータをフィルタリング
    filtered_df = df[df['品番'] == product_code]

    # フィルタリングされたデータが空かどうかをチェック
    if filtered_df.empty:
        return None

    # '発注〜順立装置入庫LT'
    filtered_df['発注〜順立装置入庫LT（非稼動日削除）'] = filtered_df.apply(calculate_business_time_order, axis=1)
    filtered_df['検収〜順立装置入庫LT（非稼動日削除）'] = filtered_df.apply(calculate_business_time_reception, axis=1)

    # フィルタリングされたデータが空かどうかを再チェック
    if filtered_df.empty:
        return None

    # '発注〜順立装置入庫LT'の中央値を計算
    median_value_order = filtered_df['発注〜順立装置入庫LT（非稼動日削除）'].median()
    median_value_reception = filtered_df['検収〜順立装置入庫LT（非稼動日削除）'].median()
    
    #確認用
    #print(filtered_df[['発注日時','順立装置入庫日時','発注〜順立装置入庫LT','発注〜順立装置入庫LT（非稼動日削除）']].head(50))

    return median_value_order, median_value_reception

def find_best_lag_range(hourly_data, hourly_target, min_lag, max_lag, label_name):
    
    """
    指定された範囲内でイベントの最適な遅れ範囲を見つける関数。
    
    Parameters:
    hourly_data (pd.Series): 1時間ごとのイベント数
    hourly_target (pd.Series): 1時間ごとのターゲットイベント数
    min_lag (int): 最小遅れ時間
    max_lag (int): 最大遅れ時間
    label_name (str): ローリング平均のラベル名
    
    Returns:
    tuple: (最適な相関係数, 最適な遅れ範囲の開始, 最適な遅れ範囲の終了)
    """
    
    best_corr = -1
    best_range_start = None
    best_range_end = None
    
    # 最適な遅れ範囲を探索
    for range_start in range(min_lag, max_lag - 1):
        for range_end in range(range_start + 2, max_lag + 1):
            lag_features = pd.DataFrame(index=hourly_data.index)
            
            # 遅れ範囲に基づいてイベント数のローリング平均を計算
            lag_features[f'{label_name}_lag_{range_start}-{range_end}'] = hourly_data.shift(range_start).rolling(window=range_end - range_start + 1).mean()
            lag_features['入庫かんばん数（t）'] = hourly_target
            
            # 欠損値を除去
            lag_features = lag_features.dropna()
            
            # 相関を計算し、最適な範囲を見つける
            corr = lag_features[f'{label_name}_lag_{range_start}-{range_end}'].corr(lag_features['入庫かんばん数（t）'])
            if corr > best_corr:
                if (range_end-range_start)<5:#時間幅を設定
                    best_corr = corr
                    best_range_start = range_start
                    best_range_end = range_end
    
    return best_corr, best_range_start, best_range_end

def create_lagged_features(hourly_data, hourly_target, hourly_leave, best_range_start, best_range_end, label_name, delivery_info, reception_times):
    
    """
    最適な遅れ範囲に基づいてイベントのローリング合計を計算し、説明変数として追加する関数。
    
    Parameters:
    hourly_data (pd.Series): 1時間ごとのイベント数
    hourly_target (pd.Series): 1時間ごとのターゲットイベント数
    best_range_start (int): 最適な遅れ範囲の開始（時間単位）
    best_range_end (int): 最適な遅れ範囲の終了（時間単位）
    label_name (str): ローリング平均のラベル名
    delivery_info (pd.Series): 1時間ごとの納入便数
    reception_times (pd.Series): 1時間ごとの平均検収時間（HH:MM:SS形式）
    
    Returns:
    pd.DataFrame: ローリング合計を含むデータフレーム
    """
    
    lag_features = pd.DataFrame(index=hourly_data.index)
    
    # 最適な遅れ範囲に基づいてローリング平均を計算し、説明変数として追加
    lag_features[f'{label_name}best（t-{best_range_start}~t-{best_range_end}）'] = hourly_data.shift(best_range_start).rolling(window=best_range_end - best_range_start + 1).sum()
    lag_features['入庫かんばん数（t）'] = hourly_target
    lag_features['出庫かんばん数（t）'] = hourly_leave
    
    # 納入かんばん数を与えた場合は、平均納入時間（XX:XX）を計算
    if label_name == '納入かんばん数':
        lag_features[f'納入便（t-{best_range_start}~t-{best_range_end}）'] = delivery_info.shift(best_range_start).rolling(window=best_range_end - best_range_start + 1).max()
        
        # 時刻データを秒に変換
        reception_times = pd.to_datetime(reception_times, format='%H:%M:%S', errors='coerce')
        seconds = reception_times.dt.hour * 3600 + reception_times.dt.minute * 60 + reception_times.dt.second

        # シフトとローリング合計を計算
        shifted_seconds = seconds.shift(best_range_start).rolling(window=best_range_end - best_range_start + 1).sum()

        # 秒を時間に戻す関数
        def seconds_to_time(seconds):
            if pd.isna(seconds):
                return pd.NaT
            total_seconds = int(seconds)
            hours = total_seconds // 3600
            if hours >= 24:
                hours = hours -24
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            return f'{hours:02}:{minutes:02}:{seconds:02}'

        # シフトされた秒を時間に戻してデータフレームに追加
        shifted_times = shifted_seconds.apply(seconds_to_time)
        lag_features[f'平均納入時間（t-{best_range_start}~t-{best_range_end}）'] = shifted_times

        # 欠損値を除去
        lag_features = lag_features.dropna()

    return lag_features

def add_part_supplier_info(df, lagged_features, part_number):
    
    """
    元のデータフレームから品番と仕入先名を抽出し、
    lagged_featuresに品番と仕入先名を結合する関数。

    Parameters:
    df (DataFrame): 元のデータフレーム
    lagged_features (DataFrame): ラグ特徴量のデータフレーム
    part_number (str): 品番

    Returns:
    DataFrame: 品番と仕入先名が追加されたlagged_features
    """
    
    # 元のデータフレームから該当品番、仕入先、仕入先工場列を抽出
    part_supplier_info = df[['品番', '仕入先名', '仕入先工場名']].drop_duplicates()
    
    #-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★
    # 特定の文字列を含む行を削除
    # 仕入先工場名は何もないものと<NULL>が混在しているものがある
    part_supplier_info = part_supplier_info[~part_supplier_info['仕入先名'].str.contains('< NULL >', na=False)].dropna(subset=['仕入先工場名'])
    #filtered_df = df[df['品番'] == part_number]
    #print(filtered_df)#仕入先名に<NULL>がある
    #-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★-★

    # indexを日付に設定
    lagged_features = lagged_features.reset_index()
    lagged_features = lagged_features.rename(columns={'イベント時間': '日時'})

    # lagged_features に品番と仕入先名を結合
    lagged_features['品番'] = part_number  # 品番を追加
    lagged_features = lagged_features.merge(part_supplier_info, on='品番', how='left')

    return lagged_features

# 特定の言葉を含む列名を見つける関数
def find_columns_with_word_in_name(df, word):
    columns_with_word = [column for column in df.columns if word in column]
    return ', '.join(columns_with_word)

def calculate_elapsed_time_since_last_dispatch(lagged_features):
    
    """
    出庫からの経過行数および出庫間隔の中央値を計算する関数。
    
    Parameters:
    lagged_features (pd.DataFrame): 出庫データを含むデータフレーム
    
    Returns:
    pd.DataFrame: 出庫からの経過行数を含むデータフレーム
    int: 出庫間隔の中央値（行数）
    """
    
    lagged_features['過去の出庫からの経過行数'] = None
    last_dispatch_index = None

    for index, row in lagged_features.iterrows():
        if last_dispatch_index is not None:
            elapsed_rows = index - last_dispatch_index
            lagged_features.at[index, '過去の出庫からの経過行数'] = elapsed_rows
        if row['出庫かんばん数（t）'] > 0:
            last_dispatch_index = index
    
    # 表示のための調整（最初の行の '過去の出庫からの経過行数' 列は None にする）
    lagged_features.at[0, '過去の出庫からの経過行数'] = None
    
    # 出庫かんばん数が1以上の行インデックスを特定
    dispatch_indices = lagged_features[lagged_features['出庫かんばん数（t）'] >= 1].index

    # 出庫間隔を計算
    dispatch_intervals = dispatch_indices.to_series().diff().dropna()

    # 出庫間隔の中央値を計算
    median_interval = dispatch_intervals.median()
    
    #print("出庫間隔の中央値（行数）:", median_interval)
    
    return lagged_features, int(median_interval)

# timedeltaをHH:MM:SS形式に変換する関数
def timedelta_to_hhmmss(td):
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"

# 仕入先便到着フラグを設定する関数
def set_arrival_flag(row, columns_delibery_num,columns_delibery_times):
    #print(row['早着'],row[columns_delibery_times],columns_delibery_num,columns_delibery_times)
    early = pd.to_datetime(row['早着'], format='%H:%M:%S').time()
    on_time = pd.to_datetime(row['定刻'], format='%H:%M:%S').time()
    late = pd.to_datetime(row['遅着'], format='%H:%M:%S')
    #print(row[columns_delibery_times])
    delivery_time = pd.to_datetime(row[columns_delibery_times], format='%H:%M:%S').time()
    
    late_plus_2hrs = (late + timedelta(hours=2)).time()
     
    if row[columns_delibery_num] != 0:
        if early < delivery_time < on_time:
            return 0 #'早着'
        elif on_time < delivery_time < late.time():
            return 1 #'定刻'
        elif late.time() < delivery_time < late_plus_2hrs:
            return 2 #'遅着'
        else:
            return 3 #'ダイヤ変更'
    else:
        return 4#'便無し'
    
def drop_columns_with_word(df, word):
    
    """
    特定の文字列を含む列を削除する関数

    Parameters:
    df (pd.DataFrame): 対象のデータフレーム
    word (str): 削除したい列名に含まれる文字列

    Returns:
    pd.DataFrame: 指定された文字列を含む列が削除されたデータフレーム
    """
    
    columns_to_drop = [column for column in df.columns if word in column]
    return df.drop(columns=columns_to_drop)

def feature_engineering(df):
    # 新しい '荷役時間' 列を計算
    df['荷役時間'] = df['荷役時間(t-4)'] + df['荷役時間(t-5)'] + df['荷役時間(t-6)']
    
    # 新しい列を初期化
    df['部品置き場からの入庫'] = 0
    df['部品置き場で滞留'] = 0
    df['定期便にモノ無し'] = 0
    
    # 条件ロジックを適用
    for index, row in df.iterrows():
        if row['荷役時間'] == 0 and row['入庫かんばん数（t）'] > 0:
            df.at[index, '部品置き場からの入庫'] = 1#row['入庫かんばん数（t）']
        elif row['荷役時間'] > 0 and row['入庫かんばん数（t）'] == 0:
            df.at[index, '部品置き場で滞留'] = 1
            df.at[index, '定期便にモノ無し'] = 1
    
    return df

# 過去X時間前からY時間前前までの平均生産台数_加重平均済を計算する関数
def calculate_window_width(data, start_hours_ago, end_hours_ago, timelag, reception_timelag):
    data = data.sort_values(by='日時')
    # 生産台数の加重平均を計算
    data[f'生産台数_加重平均（t-{end_hours_ago}~t-{timelag}）'] = data['生産台数_加重平均済'].rolling(window=start_hours_ago+1-end_hours_ago, min_periods=1).sum().shift(end_hours_ago)
    # 計画生産台数の加重平均を計算
    data[f'計画組立生産台数_加重平均（t-{end_hours_ago}~t-{timelag}）'] = data['生産台数_加重平均済'].rolling(window=timelag+1-end_hours_ago, min_periods=1).sum().shift(end_hours_ago)
    # 計画生産台数の加重平均を計算
    data[f'計画達成率_加重平均（t-{end_hours_ago}~t-{timelag}）'] = data['計画達成率_加重平均済'].rolling(window=timelag+1-end_hours_ago, min_periods=1).sum().shift(end_hours_ago)
    # 入庫かんばん数の合計を計算
    data[f'入庫かんばん数（t-{end_hours_ago}~t-{timelag}）'] = data['入庫かんばん数（t）'].rolling(window=timelag+1-end_hours_ago, min_periods=1).sum().shift(end_hours_ago)
    # 出庫かんばん数の合計を計算
    data[f'出庫かんばん数（t-{end_hours_ago}~t-{timelag}）'] = data['出庫かんばん数（t）'].rolling(window=timelag+1-end_hours_ago, min_periods=1).sum().shift(end_hours_ago)
    # 在庫増減数の合計を計算
    data[f'在庫増減数（t-{end_hours_ago}~t-{timelag}）'] = data['在庫増減数(t)'].rolling(window=timelag+1-end_hours_ago, min_periods=1).sum().shift(end_hours_ago)
    # 発注かんばん数の合計を計算
    data[f'発注かんばん数（t-{timelag}~t-{timelag*2}）'] = data['発注かんばん数(t)'].rolling(window=timelag+1, min_periods=1).sum().shift(timelag)
    # 納入かんばん数の合計を計算
    data[f'納入かんばん数（t-{reception_timelag}~t-{timelag+reception_timelag}）'] = data['納入かんばん数(t)'].rolling(window=timelag+1, min_periods=1).sum().shift(timelag)
    # 在庫数（箱）のシフト
    data[f'在庫数（箱）（t-{timelag}）'] = data['在庫数（箱）'].shift(timelag)
    
    data[f'間口_A1の充足率（t-{end_hours_ago}~t-{timelag}）'] = data['在庫数（箱）合計_A1'].rolling(window=timelag+1-end_hours_ago, min_periods=1).sum().shift(end_hours_ago)
    data[f'間口_A2の充足率（t-{end_hours_ago}~t-{timelag}）'] = data['在庫数（箱）合計_A2'].rolling(window=timelag+1-end_hours_ago, min_periods=1).sum().shift(end_hours_ago)
    data[f'間口_B1の充足率（t-{end_hours_ago}~t-{timelag}）'] = data['在庫数（箱）合計_B1'].rolling(window=timelag+1-end_hours_ago, min_periods=1).sum().shift(end_hours_ago)
    data[f'間口_B2の充足率（t-{end_hours_ago}~t-{timelag}）'] = data['在庫数（箱）合計_B2'].rolling(window=timelag+1-end_hours_ago, min_periods=1).sum().shift(end_hours_ago)
    data[f'間口_B3の充足率（t-{end_hours_ago}~t-{timelag}）'] = data['在庫数（箱）合計_B3'].rolling(window=timelag+1-end_hours_ago, min_periods=1).sum().shift(end_hours_ago)
    data[f'間口_B4の充足率（t-{end_hours_ago}~t-{timelag}）'] = data['在庫数（箱）合計_B4'].rolling(window=timelag+1-end_hours_ago, min_periods=1).sum().shift(end_hours_ago)
    
    data[f'部品置き場からの入庫（t-{end_hours_ago}~t-{timelag}）'] = data['部品置き場からの入庫'].rolling(window=timelag+1-end_hours_ago, min_periods=1).mean().shift(end_hours_ago)
    data[f'部品置き場で滞留（t-{end_hours_ago}~t-{timelag}）'] = data['部品置き場で滞留'].rolling(window=timelag+1-end_hours_ago, min_periods=1).mean().shift(end_hours_ago)
    data[f'定期便にモノ無し（t-{end_hours_ago}~t-{timelag}）'] = data['定期便にモノ無し'].rolling(window=timelag+1-end_hours_ago, min_periods=1).mean().shift(end_hours_ago)
    
    
    return data
    
def process_shiresakibin_flag(lagged_features, arrival_times_df):

    #平均納入時間がtimedelta64[ns]の型になっている。0days 00:00:00みたいな形
    #print(lagged_features.dtypes)

    # arrival_times_dfの仕入先名が一致する行を抽出、仕入先ダイヤフラグ
    # 仕入先名と発送場所名が一致する行を抽出
    matched_arrival_times_df = arrival_times_df[
        (arrival_times_df['仕入先名'].isin(lagged_features['仕入先名'])) &
        (arrival_times_df['発送場所名'].isin(lagged_features['発送場所名']))
    ]
    # arrival_times_dfの仕入先名列をlagged_featuresに結合
    lagged_features2 = lagged_features.merge(matched_arrival_times_df, on=['仕入先名','発送場所名'], how='left')

    # '納入便'を含む列名を見つける
    # columns_delivery_numは'納入便'を含む列名
    columns_delibery_num = find_columns_with_word_in_name(lagged_features2, '納入便')

    # 納入便がつく列をint型に変換
    lagged_features2[columns_delibery_num] = pd.to_numeric(lagged_features2[columns_delibery_num], errors='coerce').fillna(0).astype(int)

    # '平均納入時間' を含む列名を見つける
    # columns_delibery_timesは'平均納入時間' を含む列名
    columns_delibery_times = find_columns_with_word_in_name(lagged_features2, '平均納入時間')
    # 納入便の列に基づいて対応する「早着」「定刻」「遅着」の情報を追加
    lagged_features2['早着'] = lagged_features2.apply(lambda row: row[f'{int(row[columns_delibery_num])}便_早着'] if row[columns_delibery_num] != 0 else '00:00:00', axis=1)
    lagged_features2['定刻'] = lagged_features2.apply(lambda row: row[f'{int(row[columns_delibery_num])}便_定刻'] if row[columns_delibery_num] != 0 else '00:00:00', axis=1)
    lagged_features2['遅着'] = lagged_features2.apply(lambda row: row[f'{int(row[columns_delibery_num])}便_遅着'] if row[columns_delibery_num] != 0 else '00:00:00', axis=1)

    # timedelta形式に変換
    lagged_features2[columns_delibery_times] = pd.to_timedelta(lagged_features2[columns_delibery_times])

    # 変換を適用
    lagged_features2[columns_delibery_times] = lagged_features2[columns_delibery_times].apply(timedelta_to_hhmmss)

    # 新しい列を追加し、条件に基づいて「仕入先便到着フラグ」を設定
    lagged_features2['仕入先便到着フラグ'] = lagged_features2.apply(set_arrival_flag, columns_delibery_num=columns_delibery_num,columns_delibery_times=columns_delibery_times, axis=1)

    # 特定の文字列を含む列を削除する
    lagged_features2 = drop_columns_with_word(lagged_features2, '早着')
    lagged_features2 = drop_columns_with_word(lagged_features2, '定刻')
    lagged_features2 = drop_columns_with_word(lagged_features2, '遅着')
    lagged_features2 = drop_columns_with_word(lagged_features2, '受入')
    lagged_features2 = drop_columns_with_word(lagged_features2, '平均納入時間')

    return lagged_features2

#def visualize_stock_trend(data):
#
#    # サイドバーで開始日と終了日を選択
#    st.sidebar.header("STEP2：在庫推移可視化")
#
#    # データの最小日時と最大日時を取得
#    min_datetime = data['日時'].min()
#    max_datetime = data['日時'].max()
#
#    print(min_datetime,max_datetime)
#
#    default_values = {
#        'start_date': min_datetime.date(),
#        'end_date': max_datetime.date(),
#        'start_time': datetime.strptime("00:00", "%H:%M").time(),  # 0:00として初期化
#        'end_time': datetime.strptime("23:00", "%H:%M").time(),  # 23:00として初期化
#        'button_clicked': False
#    }
#
#    for key, value in default_values.items():
#        if key not in st.session_state:
#            st.session_state[key] = value
#
#    # サイドバーにフォームの作成
#    with st.sidebar.form(key='filter_form'):
#        st.session_state.start_date = st.date_input("開始日", st.session_state.start_date)
#        st.session_state.end_date = st.date_input("終了日", st.session_state.end_date)
#        start_time_hours = st.slider("開始時間", 0, 23, st.session_state.start_time.hour, format="%02d:00")
#        end_time_hours = st.slider("終了時間", 0, 23, st.session_state.end_time.hour, format="%02d:00")
#
#        # 時間を更新
#        st.session_state.start_time = dt_time(start_time_hours, 0)
#        st.session_state.end_time = dt_time(end_time_hours, 0)
#
#        # フォームの送信ボタン
#        submit_button = st.form_submit_button(label='適用')
#
#        if submit_button:
#            st.session_state.button_clicked = True
#
#    # フォームが送信された場合の処理
#    if submit_button:
#        st.session_state.button_clicked = True
#
#    # ボタンが押された場合のみ処理を実行
#    if st.session_state.button_clicked:
#        # 開始日時と終了日時を結合
#        start_datetime = datetime.combine(st.session_state.start_date, st.session_state.start_time)
#        end_datetime = datetime.combine(st.session_state.end_date, st.session_state.end_time)
#
#        # start_datetimeとend_datetimeに対応するインデックスを見つける
#        start_index = data.index[data['日時'] == start_datetime].tolist()
#        end_index = data.index[data['日時'] == end_datetime].tolist()
#
#        if start_index == [] or end_index == []:
#            st.session_state.error_message = "非稼動日を選択しています。"
#            st.sidebar.markdown(f"<span style='color:red;'>{st.session_state.error_message}</span>", unsafe_allow_html=True)
#            #sys.exit()
#            flag = 0
#
#        else:
#            st.write(f"開始日時: {start_datetime}, インデックス: {start_index}")
#            st.write(f"終了日時: {end_datetime}, インデックス: {end_index}")
#            flag = 1
#    else:
#        st.sidebar.warning("開始日、終了日、開始時間、終了時間を選択し、実行ボタンを押してください。")
#        flag = 0
#        #sys.exit()
#
#    return start_index,end_index,flag
#
#def display_data_app(line_data, bar_data, df2):
#
#    line_df = pd.DataFrame(line_data)
#    line_df['日時'] = pd.to_datetime(line_df['日時'], format='%Y%m%d%H')
#
#    bar_df = pd.DataFrame(bar_data)
#    bar_df['日時'] = pd.to_datetime(bar_df['日時'])
#
#    df2 = pd.DataFrame(df2)
#    df2['日時'] = pd.to_datetime(df2['日時'])
#
#    # カスタムCSSを適用して画面サイズを中央にする
#    st.markdown(
#        """
#        <style>
#        .main .block-container {
#            max-width: 60%;
#            margin-left: auto;
#            margin-right: auto;
#        }
#        </style>
#        """,
#        unsafe_allow_html=True,
#    )
#
#    # サイドバーに使い方を表示
#    #st.sidebar.header("使い方")
#    #st.sidebar.markdown("""
#    #1. 上部の折れ線グラフで全体のデータ推移を確認できます。
#    #2. 下部の棒グラフでは、特定の日時におけるデータを詳細に表示します。
#    #3. スライドバーで日時を選択し、結果が動的に変更されます。
#    #""")
#
#    # 上に折れ線グラフ
#    fig_line = go.Figure()
#    for var in line_df.columns[1:]:
#        fig_line.add_trace(go.Scatter(x=line_df['日時'].dt.strftime('%Y-%m-%d-%H'), y=line_df[var], mode='lines+markers', name=var))
#
#    #在庫増減数なので、在庫数を計算する時は、以下の処理をする
#    #        # 2つ目の折れ線グラフ
#    #        fig_line.add_trace(go.Scatter(
#    #            x=df2_subset.index.strftime('%Y-%m-%d-%H'),
#    #            #★
#    #            y=y_pred_subset+y_base_subset,
#    #            #y=y_pred_subset+df2_subset.shift(1),
#    #            mode='lines+markers',
#    #            name='AI推定値'
#    #        ))
#
#    fig_line.update_layout(
#        title="在庫推移",
#        xaxis_title="日時",
#        yaxis_title="在庫数（箱）",
#        height=500,  # 高さを調整
#        width=100,   # 幅を調整
#        margin=dict(l=0, r=0, t=30, b=0)
#    )
#
#    # 折れ線グラフを表示
#    st.plotly_chart(fig_line, use_container_width=True)
#
#    # スライドバーをメインエリアに配置
#    min_datetime = bar_df['日時'].min().to_pydatetime()
#    max_datetime = bar_df['日時'].max().to_pydatetime()
#
#    print(min_datetime,max_datetime)
#
#    #-----------------------------------------------------------------
#
#    # サイドバーに日時選択スライダーを表示
#    st.sidebar.title("要因分析")
#
#    # フォーム作成
#    with st.sidebar.form("date_selector_form"):
#        selected_datetime = st.slider(
#            "要因分析の結果を表示する日時を選択してください",
#            min_value=min_datetime,
#            max_value=max_datetime,
#            value=min_datetime,
#            format="YYYY-MM-DD HH",
#            step=pd.Timedelta(hours=1)
#        )
#        submitted = st.form_submit_button("適用")
#
#    if submitted == True:
#        # 選択された日時のデータを抽出
#        filtered_df1 = bar_df[bar_df['日時'] == pd.Timestamp(selected_datetime)]
#        filtered_df2 = df2[df2['日時'] == pd.Timestamp(selected_datetime)]
#
#        if not filtered_df1.empty:
#            st.write(f"選択された日時: {selected_datetime}")
#
#            # データを長い形式に変換
#            df1_long = filtered_df1.melt(id_vars=['日時'], var_name='変数', value_name='値')
#            # データフレームを値の降順にソート
#            df1_long = df1_long.sort_values(by='値', ascending=True)
#
#
#            # ホバーデータに追加の情報を含める
#            hover_data = {}
#            for i, row in filtered_df2.iterrows():
#                for idx, value in row.iteritems():
#                    if idx != '日時':
#                        hover_data[idx] = f"<b>日時:</b> {row['日時']}<br><b>{idx}:</b> {value:.2f}<br>"
#
#            # 横棒グラフ
#            fig_bar = px.bar(df1_long,
#                             x='値', y='変数',
#                             orientation='h',
#                             labels={'値': '寄与度（SHAP値）', '変数': '変数', '日時': '日時'},
#                             title=f"{selected_datetime}のデータ")
#
#
#            # 色の設定
#            colors = ['red' if v >= 0 else 'blue' for v in df1_long['値']]
#            # ホバーテンプレートの設定
#            # SHAP値ではないものを表示用
#            fig_bar.update_traces(
#                marker_color=colors,
#                hovertemplate=[hover_data[v] for v in df1_long['変数']]
#            )
#
#            fig_bar.update_layout(
#                title="要因分析",
#                height=500,  # 高さを調整
#                width=100,   # 幅を調整
#                margin=dict(l=0, r=0, t=30, b=0)
#            )
#
#            # 横棒グラフを表示
#            st.plotly_chart(fig_bar, use_container_width=True)
#        else:
#            st.write("データがありません")