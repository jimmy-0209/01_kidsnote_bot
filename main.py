import time
from pipeline import run_all_children

if __name__ == "__main__":
    run_all_children()
    print("10초 뒤 창이 자동으로 닫힙니다...")
    time.sleep(10)
