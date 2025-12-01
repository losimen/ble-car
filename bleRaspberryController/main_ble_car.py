import asyncio
from ble_car_driver import BleCarDriver, CarMove

async def run_car_demo():
    print('here')
    ble_car_driver = BleCarDriver()

    if not await ble_car_driver.connect():
        print("Could not connect to car. Exiting.")
        return

    try:
        await asyncio.sleep(0.5) 
        
        ble_car_driver.move(CarMove.RIGHT)
        await asyncio.sleep(3)

    finally:
        # 4. Disconnect (must be awaited)
        await ble_car_driver.disconnect()
        print("Demo complete.")

if __name__ == '__main__':
    # Start the asyncio event loop
    asyncio.run(run_car_demo())