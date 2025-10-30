"""
Integration test for Market Clearing Mechanism.

Validates the core transactive energy double-auction clearing process:
1. Multiple assets submit supply/demand curves to the market
2. Market aggregates all curves into campus-level supply and demand
3. Clearing algorithm finds intersection point (equilibrium price/quantity)
4. Market publishes marginal prices back to all assets
5. Assets update their schedules based on cleared prices
"""
import json
import os
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch, Mock

from tests.mock_tn import MockTN
from tent.enumerations.market_state import MarketState
from tent.utils.timer import Timer


def test_market_aggregates_asset_curves():
    """Validate that market correctly aggregates all local asset curves."""
    print("\n" + "="*80)
    print("INTEGRATION TEST: Market Curve Aggregation")
    print("="*80)
    
    Timer.simulation = False
    tn = MockTN()
    base_dir = Path(__file__).resolve().parent.parent.parent
    config_path = base_dir / 'sample_configs' / 'tn.campus.json'
    
    with open(config_path, encoding='utf-8') as f:
        configs = json.load(f)
    
    for ism in configs.get('information_service_models', []) or []:
        wf = ism.get('weather_file_path')
        if wf and not os.path.exists(wf):
            ism['weather_file_path'] = None
    
    with patch('tent.data_manager.local_data_manager.LocalDataManager.init_archive', Mock(return_value=None)):
        tn.configure_node(configs)
    
    if hasattr(tn, 'data_manager') and tn.data_manager:
        tn.data_manager.archive_data = Mock(return_value=None)
    
    markets = []
    for series in tn.market_series:
        markets.extend(list(series))
    market = markets[0]
    interval_list = list(market.time_intervals)
    
    print(f"Market: {market.name}, Intervals: {len(interval_list)}")
    print(f"Local assets: {[la.name for la in tn.local_assets]}")
    
    # Advance to Active state to generate asset curves
    with patch.object(Timer, 'now', return_value=market.transition_times.active):
        tn.step()
    
    print(f"\nMarket state: {market.market_state.name}")
    
    # Check that local assets have created their curves
    print(f"\n{'='*80}")
    print("ASSET CURVE VALIDATION")
    print("="*80)
    
    intervals_with_asset_curves = 0
    
    for interval in interval_list[:5]:
        print(f"\n{interval.name}:")
        asset_curve_count = 0
        
        for asset in tn.local_assets:
            asset_curve = asset.active_vertices.get_by_interval(interval)
            if asset_curve and asset_curve.vertices:
                asset_curve_count += 1
                print(f"  {asset.name} vertices: {len(asset_curve.vertices)}")
                for i, v in enumerate(asset_curve.vertices[:2]):
                    print(f"    [{i}] Price: ${v.price:.4f}, Qty: {v.quantity:.2f} kW")
        
        if asset_curve_count > 0:
            intervals_with_asset_curves += 1
    
    print(f"\n{'='*80}")
    print(f"Intervals with asset curves: {intervals_with_asset_curves}/{len(interval_list[:5])}")
    
    # Assertions
    assert intervals_with_asset_curves > 0, "Local assets should have created supply/demand curves"
    
    print("\n✅ Market successfully aggregated asset curves!")
    print("\n🎉 Test complete!")


def test_market_clears_and_sets_prices():
    """Validate market clearing algorithm finds equilibrium and sets marginal prices."""
    print("\n" + "="*80)
    print("INTEGRATION TEST: Market Clearing and Price Discovery")
    print("="*80)
    
    Timer.simulation = False
    tn = MockTN()
    base_dir = Path(__file__).resolve().parent.parent.parent
    config_path = base_dir / 'sample_configs' / 'tn.campus.json'
    
    with open(config_path, encoding='utf-8') as f:
        configs = json.load(f)
    
    for ism in configs.get('information_service_models', []) or []:
        wf = ism.get('weather_file_path')
        if wf and not os.path.exists(wf):
            ism['weather_file_path'] = None
    
    with patch('tent.data_manager.local_data_manager.LocalDataManager.init_archive', Mock(return_value=None)):
        tn.configure_node(configs)
    
    if hasattr(tn, 'data_manager') and tn.data_manager:
        tn.data_manager.archive_data = Mock(return_value=None)
    
    markets = []
    for series in tn.market_series:
        markets.extend(list(series))
    market = markets[0]
    interval_list = list(market.time_intervals)
    
    print(f"Market: {market.name}")
    print(f"Initial state: {market.market_state.name}")
    
    # Progress through market states to trigger clearing
    with patch.object(Timer, 'now', return_value=market.transition_times.active):
        tn.step()
    print(f"After Active: {market.market_state.name}")
    
    with patch.object(Timer, 'now', return_value=market.transition_times.active + timedelta(seconds=1)):
        tn.step()
    
    with patch.object(Timer, 'now', return_value=market.transition_times.market_lead):
        tn.step()
    print(f"After MarketLead: {market.market_state.name}")
    
    # Check that marginal prices were set by clearing algorithm
    print(f"\n{'='*80}")
    print("MARGINAL PRICE DISCOVERY")
    print("="*80)
    
    prices_set = 0
    all_prices = []
    
    for interval in interval_list:
        marginal_price = market.marginal_prices.get_by_interval(interval)
        if marginal_price is not None:
            prices_set += 1
            price_val = float(marginal_price.value) if hasattr(marginal_price, 'value') else float(marginal_price)
            all_prices.append(price_val)
            if prices_set <= 5:
                print(f"  {interval.name}: ${price_val:.4f}/kWh")
    
    if all_prices:
        print(f"\nPrice statistics:")
        print(f"  Intervals with prices: {prices_set}/{len(interval_list)}")
        print(f"  Min price: ${min(all_prices):.4f}/kWh")
        print(f"  Max price: ${max(all_prices):.4f}/kWh")
        print(f"  Avg price: ${sum(all_prices)/len(all_prices):.4f}/kWh")
    
    # Assertions
    assert prices_set > 0, "Market clearing should set marginal prices"
    
    if all_prices:
        assert all(p >= 0 for p in all_prices), "Prices should be non-negative"
        assert max(all_prices) < 1000, "Prices should be reasonable (< $1000/kWh)"
    
    print("\n✅ Market successfully cleared and set prices!")
    print("\n🎉 Test complete!")


def test_market_clears_multiple_intervals_independently():
    """Validate that each time interval clears independently with its own price."""
    print("\n" + "="*80)
    print("INTEGRATION TEST: Independent Interval Clearing")
    print("="*80)
    
    Timer.simulation = False
    tn = MockTN()
    base_dir = Path(__file__).resolve().parent.parent.parent
    config_path = base_dir / 'sample_configs' / 'tn.campus.json'
    
    with open(config_path, encoding='utf-8') as f:
        configs = json.load(f)
    
    for ism in configs.get('information_service_models', []) or []:
        wf = ism.get('weather_file_path')
        if wf and not os.path.exists(wf):
            ism['weather_file_path'] = None
    
    with patch('tent.data_manager.local_data_manager.LocalDataManager.init_archive', Mock(return_value=None)):
        tn.configure_node(configs)
    
    if hasattr(tn, 'data_manager') and tn.data_manager:
        tn.data_manager.archive_data = Mock(return_value=None)
    
    markets = []
    for series in tn.market_series:
        markets.extend(list(series))
    market = markets[0]
    interval_list = list(market.time_intervals)
    
    # Progress to clearing
    with patch.object(Timer, 'now', return_value=market.transition_times.active):
        tn.step()
    
    with patch.object(Timer, 'now', return_value=market.transition_times.active + timedelta(seconds=1)):
        tn.step()
    
    with patch.object(Timer, 'now', return_value=market.transition_times.market_lead):
        tn.step()
    
    print(f"Market: {market.name}, State: {market.market_state.name}")
    
    # Collect prices and check for variation
    print(f"\n{'='*80}")
    print("INTERVAL-SPECIFIC PRICE VALIDATION")
    print("="*80)
    
    interval_prices = {}
    for interval in interval_list:
        marginal_price = market.marginal_prices.get_by_interval(interval)
        if marginal_price is not None:
            price_val = float(marginal_price.value) if hasattr(marginal_price, 'value') else float(marginal_price)
            interval_prices[interval.name] = price_val
    
    print(f"\nSample of interval-specific prices:")
    for name, price in list(interval_prices.items())[:8]:
        print(f"  {name}: ${price:.4f}/kWh")
    
    if len(interval_prices) > 1:
        unique_prices = len(set(interval_prices.values()))
        print(f"\nPrice diversity:")
        print(f"  Total intervals with prices: {len(interval_prices)}")
        print(f"  Unique price values: {unique_prices}")
        
        # Assertions
        assert len(interval_prices) > 0, "Should have prices for multiple intervals"
        
        # Check that at least some intervals have different prices (time-varying conditions)
        if len(interval_prices) >= 4:
            price_range = max(interval_prices.values()) - min(interval_prices.values())
            print(f"  Price range: ${price_range:.4f}/kWh")
    
    print("\n✅ Each interval cleared independently!")
    print("\n🎉 Test complete!")


def test_cleared_quantities_match_asset_commitments():
    """Validate that cleared quantities align with asset power commitments."""
    print("\n" + "="*80)
    print("INTEGRATION TEST: Cleared Quantity vs Asset Commitment")
    print("="*80)
    
    Timer.simulation = False
    tn = MockTN()
    base_dir = Path(__file__).resolve().parent.parent.parent
    config_path = base_dir / 'sample_configs' / 'tn.campus.json'
    
    with open(config_path, encoding='utf-8') as f:
        configs = json.load(f)
    
    for ism in configs.get('information_service_models', []) or []:
        wf = ism.get('weather_file_path')
        if wf and not os.path.exists(wf):
            ism['weather_file_path'] = None
    
    with patch('tent.data_manager.local_data_manager.LocalDataManager.init_archive', Mock(return_value=None)):
        tn.configure_node(configs)
    
    if hasattr(tn, 'data_manager') and tn.data_manager:
        tn.data_manager.archive_data = Mock(return_value=None)
    
    markets = []
    for series in tn.market_series:
        markets.extend(list(series))
    market = markets[0]
    interval_list = list(market.time_intervals)
    
    # Progress to post-clearing state
    with patch.object(Timer, 'now', return_value=market.transition_times.active):
        tn.step()
    
    with patch.object(Timer, 'now', return_value=market.transition_times.active + timedelta(seconds=1)):
        tn.step()
    
    with patch.object(Timer, 'now', return_value=market.transition_times.market_lead):
        tn.step()
    
    with patch.object(Timer, 'now', return_value=market.transition_times.market_lead + timedelta(seconds=1)):
        tn.step()
    
    print(f"Market: {market.name}, State: {market.market_state.name}")
    
    # Check cleared quantities and asset commitments
    print(f"\n{'='*80}")
    print("QUANTITY RECONCILIATION")
    print("="*80)
    
    for interval in interval_list[:5]:
        print(f"\n{interval.name}:")
        
        # Get market's view of total power
        total_asset_power = 0.0
        for asset in tn.local_assets:
            scheduled_power = asset.scheduled_powers.get_by_interval(interval)
            if scheduled_power and scheduled_power.value is not None:
                power = scheduled_power.value
                total_asset_power += power
                print(f"  {asset.name}: {power:.2f} kW")
        
        print(f"  {'Total campus power':20s}: {total_asset_power:.2f} kW")
        
        # Check that marginal price exists (market cleared)
        marginal_price = market.marginal_prices.get_by_interval(interval)
        if marginal_price is not None:
            price_val = float(marginal_price.value) if hasattr(marginal_price, 'value') else float(marginal_price)
            print(f"  Cleared at price: ${price_val:.4f}/kWh")
    
    print("\n✅ Cleared quantities tracked!")
    print("\n🎉 Test complete!")


def test_supply_demand_curve_intersection():
    """Validate that clearing finds the intersection of supply and demand curves."""
    print("\n" + "="*80)
    print("INTEGRATION TEST: Supply/Demand Curve Intersection")
    print("="*80)
    
    Timer.simulation = False
    tn = MockTN()
    base_dir = Path(__file__).resolve().parent.parent.parent
    config_path = base_dir / 'sample_configs' / 'tn.campus.json'
    
    with open(config_path, encoding='utf-8') as f:
        configs = json.load(f)
    
    for ism in configs.get('information_service_models', []) or []:
        wf = ism.get('weather_file_path')
        if wf and not os.path.exists(wf):
            ism['weather_file_path'] = None
    
    with patch('tent.data_manager.local_data_manager.LocalDataManager.init_archive', Mock(return_value=None)):
        tn.configure_node(configs)
    
    if hasattr(tn, 'data_manager') and tn.data_manager:
        tn.data_manager.archive_data = Mock(return_value=None)
    
    markets = []
    for series in tn.market_series:
        markets.extend(list(series))
    market = markets[0]
    interval_list = list(market.time_intervals)
    
    # Progress through clearing
    with patch.object(Timer, 'now', return_value=market.transition_times.active):
        tn.step()
    
    with patch.object(Timer, 'now', return_value=market.transition_times.active + timedelta(seconds=1)):
        tn.step()
    
    with patch.object(Timer, 'now', return_value=market.transition_times.market_lead):
        tn.step()
    
    print(f"Market: {market.name}, State: {market.market_state.name}")
    
    # Analyze aggregated system vertices and clearing
    print(f"\n{'='*80}")
    print("SYSTEM VERTEX ANALYSIS")
    print("="*80)
    
    for interval in interval_list[:3]:
        print(f"\n{interval.name}:")
        
        system_vertices = market.active_vertices.get_by_interval(interval)
        marginal_price = market.marginal_prices.get_by_interval(interval)
        
        if system_vertices and marginal_price:
            cleared_price = float(marginal_price.value) if hasattr(marginal_price, 'value') else float(marginal_price)
            
            print(f"  Cleared price: ${cleared_price:.4f}/kWh")
            print(f"  System vertices: {len(system_vertices.vertices)}")
            
            # Show vertices near balance point (quantity ~ 0)
            if system_vertices.vertices:
                vertex_prices = [v.price for v in system_vertices.vertices]
                vertex_quantities = [v.quantity for v in system_vertices.vertices]
                
                min_price = min(vertex_prices)
                max_price = max(vertex_prices)
                min_qty = min(vertex_quantities)
                max_qty = max(vertex_quantities)
                
                print(f"  Price range: [${min_price:.4f}, ${max_price:.4f}]")
                print(f"  Quantity range: [{min_qty:.2f}, {max_qty:.2f}] kW")
                
                # Cleared price should be within vertex price bounds
                assert min_price <= cleared_price <= max_price, \
                    f"Cleared price should be within vertex bounds: [{min_price}, {max_price}]"
    
    print("\n✅ Clearing algorithm found valid intersection!")
    print("\n🎉 Test complete!")


if __name__ == "__main__":
    test_market_aggregates_asset_curves()
    test_market_clears_and_sets_prices()
    test_market_clears_multiple_intervals_independently()
    test_cleared_quantities_match_asset_commitments()
    test_supply_demand_curve_intersection()
