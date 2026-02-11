#!/usr/bin/env python3.11

from flask import Flask, jsonify, send_file, render_template_string, request
import os
import requests
import random
import json
import io
import time
import threading
import logging
import hashlib

from node_controller.controller.controller import Controller
from node_controller.gateway.utils import from_gas_amount, to_gas_amount
from node_controller.gateway.protos import celaut_pb2, celaut_pb2_grpc

from generate_commitment import generate_gop_commitment


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app.log'),
    filemode='w'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

SECRET_S_HEX = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

APP_DIR = "."
FREQUENCY = 1
TIME_FOR_SOLVER_START = 90

BOARD_ROWS = 20
BOARD_COLS = 40

snake_globals = []
apple_globals = []
game_over_globals = False
game_started_globals = False
game_history_globals = []
game_over_reason_globals = None
current_score_globals = 0
current_move_made_globals = None
current_solver_id_globals = None
participation_output_globals = {}
current_seed_globals = None

game_thread = None
game_lock = threading.Lock()
stop_game_event = threading.Event()

controller = Controller(
    debug=lambda s: logger.info('Node Controller: %s', s),
    app_dir=APP_DIR,
    config_file="./__config__"   # To be used locally with "nodo ggconf ." command
)
solver_url = ""

def generate_dummy_score_list(true_score, size=5, max_possible_score=BOARD_ROWS * BOARD_COLS):
    if size <= 0:
        return []
    true_score_int = int(true_score)
    
    min_s = 1
    if max_possible_score < min_s:
        max_possible_score = min_s

    scores = {true_score_int}
    attempts = 0
    power_bias = 2.5

    while len(scores) < size and attempts < size * 30:
        value_range = max_possible_score - min_s
        dummy_score = min_s
        if value_range >= 0:
            skew_factor = random.random() ** power_bias
            offset = int(skew_factor * (value_range + 1))
            dummy_score = min_s + offset
            dummy_score = min(max_possible_score, dummy_score)
            dummy_score = max(min_s, dummy_score)
        else:
            dummy_score = min_s
        
        scores.add(dummy_score)
        attempts += 1
    
    score_list = list(scores)
    
    idx_filler_fallback = 0
    while len(score_list) < size:
        filler_score = random.randint(min_s, max_possible_score)
        if filler_score not in score_list :
             score_list.append(filler_score)
        else:
            idx_filler_fallback+=1
            alt_filler = (true_score_int + idx_filler_fallback)
            if alt_filler > max_possible_score:
                alt_filler = min_s + (alt_filler % (max_possible_score - min_s + 1)) if (max_possible_score - min_s + 1) > 0 else min_s
            if alt_filler < min_s: alt_filler = min_s
            
            if alt_filler not in score_list:
                 score_list.append(alt_filler)
            elif len(score_list) < size :
                 score_list.append(random.randint(min_s,max_possible_score))

    final_list = []
    if true_score_int in score_list:
        final_list.append(true_score_int)
        score_list.remove(true_score_int)
    else:
        final_list.append(true_score_int)

    random.shuffle(score_list)
    
    for s_val in score_list:
        if len(final_list) < size:
            if s_val not in final_list:
                final_list.append(s_val)
    
    attempts = 0
    while len(final_list) < size:
        val = random.randint(min_s, max_possible_score)
        if min_s == max_possible_score and true_score_int == min_s and len(final_list) > 0:
             final_list.append(true_score_int)
        elif val not in final_list:
             final_list.append(val)
        elif len(final_list) < size and attempts > 20 :
             final_list.append(val)
        attempts +=1
        if attempts > size * 5 and len(final_list) < size:
            while len(final_list) < size: final_list.append(random.randint(min_s,max_possible_score))

    random.shuffle(final_list)
    return final_list[:size]


def generate_apple_internal(rng):
    if len(snake_globals) >= BOARD_ROWS * BOARD_COLS:
        return None
    while True:
        pos = [rng.randint(0, BOARD_ROWS - 1), rng.randint(0, BOARD_COLS - 1)]
        if pos not in snake_globals:
            return pos

def record_current_state_internal(move_command=None):
    global game_history_globals, snake_globals, apple_globals, game_over_globals, game_over_reason_globals, current_score_globals, current_seed_globals
    current_snake_state = [list(segment) for segment in snake_globals]
    current_apple_state = list(apple_globals) if apple_globals else []
    game_history_globals.append({
        'snake': current_snake_state,
        'apple': current_apple_state,
        'score': current_score_globals,
        'game_over': game_over_globals,
        'game_over_reason': game_over_reason_globals if game_over_globals else None,
        'move_made': move_command,
        'seed': current_seed_globals
    })

def initialize_game_for_thread(seed=None):
    logger.info("initialize_game_for_thread: Starting game initialization logic.")
    global snake_globals, apple_globals, game_over_globals, game_started_globals
    global game_history_globals, game_over_reason_globals, current_score_globals, current_move_made_globals
    global current_seed_globals

    if seed is None:
        seed = random.Random().randint(0, 2**64)  # 2**64 like Minecraft seeds.
    else:
        seed = int(seed) if type(seed) in [int, float] else hash(seed) # Like Minecraft seed strings.

    with game_lock:
        current_seed_globals = seed
        rng = random.Random(seed)
        logger.info(f"Game initialized with seed: {seed}")

        snake_globals = [[rng.randint(0, BOARD_ROWS - 1), rng.randint(0, BOARD_COLS - 1)]]
        apple_globals = generate_apple_internal(rng)
        game_history_globals = []
        game_started_globals = True
        game_over_globals = False
        game_over_reason_globals = None
        current_score_globals = len(snake_globals)
        current_move_made_globals = "Start"
        if apple_globals is None:
            game_over_globals = True
            game_over_reason_globals = "Board full at start (could not generate apple)"
        record_current_state_internal(current_move_made_globals)
    return not game_over_globals

def step_game_for_thread():
    global snake_globals, apple_globals, game_over_globals, game_started_globals
    global game_over_reason_globals, current_score_globals, current_move_made_globals, current_seed_globals

    if not game_started_globals or game_over_globals:
        return
    move_command_from_solver = None
    try:
        request_url = solver_url
        if not solver_url.startswith("http://") and not solver_url.startswith("https://"):
            request_url = "http://" + solver_url
        
        response = requests.post(request_url + '/move',
                                 json={'snake': snake_globals, 'apple': apple_globals, 'board_rows': BOARD_ROWS, 'board_cols': BOARD_COLS},
                                 timeout=FREQUENCY*0.8)
        response.raise_for_status()
        move_data = response.json()
        move_command_from_solver = move_data['move']
    except requests.exceptions.Timeout:
        game_over_globals = True
        game_over_reason_globals = "Solver Error: Timeout"
    except requests.exceptions.RequestException as e:
        game_over_globals = True
        game_over_reason_globals = f"Solver Error: {type(e).__name__}"
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        game_over_globals = True
        game_over_reason_globals = f"Solver Error: Invalid or malformed response ({type(e).__name__})"

    if not move_command_from_solver and not game_over_globals:
        game_over_globals = True
        if not game_over_reason_globals: game_over_reason_globals = "Solver Error: No move obtained"

    current_move_made_globals = move_command_from_solver
    if game_over_globals:
        record_current_state_internal(current_move_made_globals)
        return

    head = snake_globals[0]
    new_head = list(head)
    if move_command_from_solver == 'UP': new_head[0] -= 1
    elif move_command_from_solver == 'DOWN': new_head[0] += 1
    elif move_command_from_solver == 'LEFT': new_head[1] -= 1
    elif move_command_from_solver == 'RIGHT': new_head[1] += 1
    else:
        game_over_globals = True
        game_over_reason_globals = f"Solver Error: Invalid move '{move_command_from_solver}'"
    
    if not game_over_globals:
        if not (0 <= new_head[0] < BOARD_ROWS and 0 <= new_head[1] < BOARD_COLS):
            game_over_globals = True
            game_over_reason_globals = "Collision with wall"
        else:
            ate_apple = (new_head == apple_globals)
            relevant_snake_body = snake_globals if ate_apple else snake_globals[:-1]
            if new_head in relevant_snake_body:
                 game_over_globals = True
                 game_over_reason_globals = "Collision with self (body)"
            
            if not game_over_globals:
                if ate_apple:
                    snake_globals = [new_head] + snake_globals
                    current_score_globals = len(snake_globals)
                    if len(snake_globals) == BOARD_ROWS * BOARD_COLS:
                        game_over_globals = True
                        game_over_reason_globals = "Victory! Snake filled the board."
                        apple_globals = []
                    else:
                        rng_seed = current_seed_globals
                        if rng_seed is None:
                            raise Exception("Any seed on the game. This shouldn't happend.")
                        rng = random.Random(rng_seed)
                        apple_globals = generate_apple_internal(rng)
                        if apple_globals is None:
                            game_over_globals = True
                            game_over_reason_globals = "Board full (could not generate new apple after eating)"
                else:
                    snake_globals = [new_head] + snake_globals[:-1]
    
    record_current_state_internal(current_move_made_globals)


def game_loop_background():
    global game_started_globals, game_over_globals, game_over_reason_globals, participation_output_globals, current_solver_id_globals, current_score_globals, game_history_globals, solver_url, current_seed_globals

    logger.info(f"Game thread started. Waiting {TIME_FOR_SOLVER_START}s for solver ({solver_url}) to stabilize...")
    wait_interval = 1
    total_waited = 0
    solver_stabilized = True
    while total_waited < TIME_FOR_SOLVER_START:
        if stop_game_event.is_set():
            logger.info("Game stopped during solver initialization wait.")
            solver_stabilized = False
            break
        time.sleep(wait_interval)
        total_waited += wait_interval
    
    if not solver_stabilized:
        with game_lock:
            game_over_globals = True
            game_over_reason_globals = "Stopped during solver initialization."
            game_started_globals = False
            participation_output_globals = {"error": game_over_reason_globals}
            if current_solver_id_globals:
                 participation_output_globals["solver_id"] = current_solver_id_globals
        return

    game_can_start = False
    game_can_start = initialize_game_for_thread(current_seed_globals)
    if not game_can_start:
        logger.warning("Game initialization resulted in game over. Main loop will not run.")
    else:
        MAX_STEPS = BOARD_ROWS * BOARD_COLS * 2
        steps_taken = 0
        while not stop_game_event.is_set() and steps_taken < MAX_STEPS:
            with game_lock:
                if game_over_globals:
                    logger.info("Game over detected in loop, exiting.")
                    break
                step_game_for_thread()
                steps_taken += 1
                if game_over_globals and not game_over_reason_globals:
                     if steps_taken >= MAX_STEPS and not stop_game_event.is_set():
                        game_over_reason_globals = "Maximum steps reached in live game."
                     elif not stop_game_event.is_set():
                        game_over_reason_globals = game_over_reason_globals or "Ended by unspecified internal condition"
            time.sleep(FREQUENCY)
    
    with game_lock:
        if not game_over_globals:
            game_over_globals = True
            if stop_game_event.is_set() and not game_over_reason_globals:
                game_over_reason_globals = "Stopped by user."
            elif 'steps_taken' in locals() and steps_taken >= MAX_STEPS and not game_over_reason_globals:
                game_over_reason_globals = "Maximum steps reached."
            elif not game_over_reason_globals:
                 game_over_reason_globals = "Game loop finished or not started."
            if game_started_globals :
                 record_current_state_internal(current_move_made_globals)

        game_started_globals = False
        logger.info(f"Game loop thread finished. Reason: {game_over_reason_globals}")

        if current_solver_id_globals:
            try:
                logger.info("Generando datos de participación...")
                true_score_for_commitment = int(current_score_globals)
                game_logs_json = json.dumps(game_history_globals)
                hash_logs_bytes = hashlib.blake2b(game_logs_json.encode('utf-8'), digest_size=32).digest()
                hash_logs_hex = hash_logs_bytes.hex()
                solver_id_for_commitment = str(current_solver_id_globals)
                initial_score_list = generate_dummy_score_list(
                    true_score_for_commitment, size=5, max_possible_score=(BOARD_ROWS * BOARD_COLS)
                )
                commitment_c = generate_gop_commitment(
                    solver_id=solver_id_for_commitment, score=true_score_for_commitment,
                    hash_logs_hex=hash_logs_hex, secret_s_hex=SECRET_S_HEX
                )
                participation_output_globals = {
                    "solver_id": solver_id_for_commitment,
                    "true_score": true_score_for_commitment,
                    "hash_logs_hex": hash_logs_hex,
                    "commitment_c_hex": commitment_c,
                    "score_list": initial_score_list,
                    "seed": current_seed_globals
                }
                logger.info(f"Participation data generated: {participation_output_globals}")
            except Exception as e:
                logger.error(f"Error generating participation data: {e}", exc_info=True)
                participation_output_globals = {"error": f"Error generating data: {str(e)}"}
        else:
            logger.warning("Participation data not generated because current_solver_id_globals is not defined.")
            participation_output_globals = {"error": "Solver ID not available (not loaded or error occurred)."}


@app.route('/')
def index():
    return render_template_string(HTML_CONTENT,
                                  BOARD_ROWS=BOARD_ROWS,
                                  BOARD_COLS=BOARD_COLS,
                                  FREQUENCY=FREQUENCY)

@app.route('/start_live_game', methods=['POST'])
def start_live_game():
    global game_thread, game_started_globals, stop_game_event, controller, solver_url
    global current_solver_id_globals, participation_output_globals, current_seed_globals
    with game_lock:
        if game_thread and game_thread.is_alive():
            return jsonify({"success": False, "message": "A live game is already in progress."}), 400
        
        game_history_globals.clear()
        stop_game_event.clear()
        game_over_globals = False
        game_over_reason_globals = None
        current_score_globals = 0
        snake_globals.clear()
        apple_globals.clear()
        current_move_made_globals = None
        current_solver_id_globals = None
        solver_url = ""
        participation_output_globals = {}
        current_seed_globals = None

        seed_input = request.form.get('seed')
        if seed_input:
            try:
                current_seed_globals = int(seed_input)
            except ValueError:
                return jsonify({"success": False, "message": "Invalid seed format. Please enter an integer."}), 400
        
        os.makedirs("__block__", exist_ok=True)

        if 'solverFile' not in request.files:
            logger.error("No solver file provided. A .celaut.bee file is required to start.")
            return jsonify({"success": False, "message": "A solver file (.celaut.bee) is required to start a new game."}), 400

        solver_file = request.files['solverFile']
        file_path = os.path.join(APP_DIR, "solver.celaut.bee")
        solver_file.save(file_path)
        logger.info(f"Solver file '{solver_file.filename}' saved as solver.celaut.bee.")

        solver_config = celaut_pb2.Configuration(
                initial_gas_amount=to_gas_amount(10**10)
            )
        logger.info("Created solver configuration object.")
        local_solver_instance_obj = None
        try:
            solver_interface = controller.add_bee_file(
                file_path=file_path,
                config=solver_config
            )
            current_solver_id_globals = solver_interface.sc.service_hash
            logger.info(f"Solver loaded. ID (Service Hash): {current_solver_id_globals}")
            
            local_solver_instance_obj = solver_interface.get_instance(max_attempts=5)
            solver_url = local_solver_instance_obj.uri
            logger.info(f"Solver instance created: {local_solver_instance_obj}, URL: {solver_url}")

        except Exception as e:
            logger.error(f"Error configuring or instantiating solver from .celaut.bee: {e}", exc_info=True)
            if os.path.exists(file_path):
                try: os.remove(file_path)
                except OSError as ose: logger.error(f"Error deleting .celaut.bee file: {ose}")
            if local_solver_instance_obj and hasattr(local_solver_instance_obj, 'stop'):
                 try: local_solver_instance_obj.stop()
                 except Exception as ex_stop: logger.error(f"Error stopping solver instance: {ex_stop}")
            return jsonify({"success": False, "message": f"Error with solver file: {str(e)}"}), 500
        
        if not current_solver_id_globals or not solver_url:
            logger.error(f"Critical failure post-processing: current_solver_id ({current_solver_id_globals}) or solver_url ({solver_url}) not configured.")
            if local_solver_instance_obj and hasattr(local_solver_instance_obj, 'stop'):
                 try: local_solver_instance_obj.stop()
                 except Exception as ex_stop: logger.error(f"Error stopping solver instance: {ex_stop}")
            return jsonify({"success": False, "message": "Internal error configuring solver after file upload."}), 500
                
        stop_game_event.clear()
        game_thread = threading.Thread(target=game_loop_background)
        game_thread.start()
    return jsonify({"success": True, "message": "Processing solver and starting game..."})


@app.route('/stop_live_game', methods=['POST'])
def stop_live_game_endpoint():
    global game_thread, game_started_globals, stop_game_event, game_over_globals, game_over_reason_globals
    logger.info("Request to stop game received.")
    stop_game_event.set()
    message = "Stop signal sent to game loop."
    status_code = 200

    join_timeout = (FREQUENCY * 2) + 1
    if TIME_FOR_SOLVER_START > join_timeout :
        join_timeout = TIME_FOR_SOLVER_START + (FREQUENCY * 2) + 1


    if game_thread and game_thread.is_alive():
        logger.info(f"Waiting for game thread to finish (timeout {join_timeout}s)...")
        game_thread.join(timeout=join_timeout)
        if game_thread.is_alive():
            message += " Game thread might still be finalizing (timeout exceeded)."
            logger.warning("Game thread did not finish within timeout after stop signal.")
            with game_lock:
                if not game_over_globals:
                    game_over_globals = True
                    game_over_reason_globals = game_over_reason_globals or "Stopped by user (thread finalization timeout)."
                game_started_globals = False
        else:
            message += " Game thread has finished."
            logger.info("Game thread finished correctly after stop signal.")
    else:
         message = "No live game was active or thread had already finished."
         logger.info("No active game thread to stop or it had already finished.")

    with game_lock:
        if not game_started_globals and game_over_globals :
            pass
        elif not game_started_globals and not game_over_globals and stop_game_event.is_set():
             game_over_globals = True
             game_over_reason_globals = game_over_reason_globals or "Stopped by user (post-thread state)."
        if game_over_globals:
             game_started_globals = False

    logger.info(f"Post-stop state: game_started={game_started_globals}, game_over={game_over_globals}, reason='{game_over_reason_globals}'")
    return jsonify({"success": True, "message": message}), status_code


@app.route('/get_live_game_state', methods=['GET'])
def get_live_game_state():
    global game_thread, participation_output_globals, current_seed_globals
    try:
        with game_lock:
            # Case 1: Solver is actively being prepared / game is about to start
            if game_thread and game_thread.is_alive() and \
               not game_started_globals and \
               not game_over_globals and \
               not game_history_globals:
                return jsonify({
                    "game_active": False,
                    "solver_is_starting": True,
                    "game_over": False,
                    "message": "Solver is initializing, game will start shortly...",
                    "snake": [list(s) for s in snake_globals],
                    "apple": list(apple_globals) if apple_globals else [],
                    "score": current_score_globals,
                    "game_over_reason": None,
                    "move_made": current_move_made_globals,
                    "game_history_globals_length_DEBUG": len(game_history_globals),
                    "participation_data_ready": False,
                    "seed": current_seed_globals
                })

            # Case 2: No game has been started yet, or the system is idle. (Covers initial page load)
            if not game_started_globals and not game_over_globals:
                return jsonify({
                    "game_active": False,
                    "solver_is_starting": False,
                    "game_over": False, 
                    "message": "No active game or not started. Ready to begin.",
                    "snake": [], "apple": [], "score": 0,
                    "game_over_reason": "Not started", "move_made": None,
                    "game_history_globals_length_DEBUG": 0,
                    "participation_data_ready": False,
                    "seed": current_seed_globals
                })

            current_display_state = {
                'snake': [list(s) for s in snake_globals],
                'apple': list(apple_globals) if apple_globals else [],
                'score': current_score_globals,
                'game_over': game_over_globals,
                'game_over_reason': game_over_reason_globals,
                'move_made': current_move_made_globals,
                'game_active': game_started_globals,
                "solver_is_starting": False, 
                "game_history_globals_length_DEBUG": len(game_history_globals),
                "seed": current_seed_globals
            }
            
            required_participation_keys = ["true_score", "solver_id", "hash_logs_hex", "commitment_c_hex", "score_list", "seed"]

            if game_over_globals:
                if isinstance(participation_output_globals, dict):
                    if "error" in participation_output_globals:
                        current_display_state['participation_data_ready'] = False
                        current_display_state['participation_data_error'] = participation_output_globals["error"]
                    elif all(k in participation_output_globals for k in required_participation_keys):
                        current_display_state['participation_data_ready'] = True
                        for key in required_participation_keys:
                            if key in participation_output_globals:
                                current_display_state[key] = participation_output_globals[key]
                    else:
                        current_display_state['participation_data_ready'] = False
                        current_display_state['participation_data_error'] = "Participation data (server-side) incomplete or pending."
                else:
                    current_display_state['participation_data_ready'] = False
                    current_display_state['participation_data_error'] = "Internal error: participation data state corrupted."
                    logger.error(f"CRITICAL: participation_output_globals is not a dict: {type(participation_output_globals)}")
            else:
                current_display_state['participation_data_ready'] = False
                
            return jsonify(current_display_state)
    except Exception as e_route:
        logger.error(f"UNHANDLED EXCEPTION in /get_live_game_state: {e_route}", exc_info=True)
        return jsonify({
            "game_active": False, "game_over": True, "score":0, "snake":[], "apple":[],
            "message": "Internal server error fetching game state.",
            "game_over_reason": "Server Error",
            "participation_data_ready": False,
            "participation_data_error": "Server error prevented fetching data.",
            "seed": None
        }), 500


@app.route('/download_history', methods=['GET'])
def download_history_route():
    global game_history_globals
    with game_lock:
        if not game_history_globals:
            return jsonify({"message": "No game history recorded."}), 404
        history_for_download = [dict(frame) for frame in game_history_globals]

    str_io = io.StringIO()
    json.dump(history_for_download, str_io, indent=2)
    str_io.seek(0)
    mem_io = io.BytesIO()
    mem_io.write(str_io.getvalue().encode('utf-8'))
    mem_io.seek(0)
    str_io.close()
    
    return send_file(
        mem_io,
        as_attachment=True,
        download_name='snake_live_game_history.json',
        mimetype='application/json'
    )

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Game of Prompts - Snake</title>
    <style>
        :root {
            --body-bg: #1e1e1e; 
            --container-bg: #2a2a2a; 
            --text-color: #00ff41; /* Bright green text */
            --primary-color: #00e030; /* Slightly darker green for primary actions */
            --primary-hover: #00c028;
            --secondary-color: #999999; /* Medium gray for secondary text/elements */
            --success-color: var(--primary-color); 
            --danger-color: #ff4100; /* Bright red-orange for danger/apple */
            --warning-color: #ffff00; 
            --info-color: #00ffff;   
            --light-gray: #4a4a4a; /* Darker gray for borders on dark theme */
            --border-radius: 0px; 
            --box-shadow: none; 
            --font-family: 'Courier New', Courier, monospace; 
        }

        body {
            font-family: var(--font-family);
            display: flex;
            flex-direction: column;
            align-items: center;
            margin: 0;
            padding: 20px;
            background-color: var(--body-bg);
            color: var(--text-color);
            line-height: 1.6;
        }

        .page-container {
            width: 100%;
            max-width: 960px; 
            margin: 0 auto;
        }
        
        header {
            text-align: center;
            margin-bottom: 30px;
            border-bottom: 2px solid var(--primary-color);
            padding-bottom: 15px;
        }

        header h1 {
            color: var(--primary-color);
            font-weight: normal; 
            font-size: 2.8rem; 
            text-transform: uppercase;
            letter-spacing: 2px;
        }

        .section {
            background-color: var(--container-bg);
            padding: 20px; 
            border-radius: var(--border-radius);
            border: 1px solid var(--light-gray);
            box-shadow: var(--box-shadow);
            margin-bottom: 30px; 
        }

        .section h2 {
            margin-top: 0;
            margin-bottom: 20px;
            font-size: 1.5rem; 
            color: var(--text-color); 
            border-bottom: 1px solid var(--light-gray);
            padding-bottom: 10px;
            font-weight: normal;
            text-transform: uppercase;
        }
         .section h3 {
            margin-top: 20px;
            margin-bottom: 12px;
            font-size: 1.2rem;
            color: var(--text-color); 
            font-weight: normal;
            text-transform: uppercase;
        }
        
        button {
            padding: 10px 15px; 
            font-size: 0.95rem;
            border: 1px solid var(--primary-color);
            border-radius: var(--border-radius);
            cursor: pointer;
            background-color: transparent; 
            color: var(--primary-color); 
            transition: background-color 0.2s ease-in-out, color 0.2s ease-in-out;
            box-shadow: var(--box-shadow);
            text-transform: uppercase;
            font-weight: normal;
        }
        button:hover:not(:disabled) {
            background-color: var(--primary-color);
            color: var(--body-bg); 
        }
        button:active:not(:disabled) {
            background-color: var(--primary-hover);
            color: var(--body-bg);
        }
        button:disabled {
            background-color: var(--light-gray) !important; 
            color: var(--secondary-color) !important;
            border-color: var(--secondary-color) !important;
            cursor: not-allowed;
        }

        .button-primary { border-color: var(--primary-color); color: var(--primary-color); }
        .button-primary:hover:not(:disabled) { background-color: var(--primary-color); color: var(--body-bg); }
        
        .button-danger { border-color: var(--danger-color); color: var(--danger-color); }
        .button-danger:hover:not(:disabled) { background-color: var(--danger-color); color: var(--body-bg); }

        .button-success { border-color: var(--success-color); color: var(--success-color); }
        .button-success:hover:not(:disabled) { background-color: var(--success-color); color: var(--body-bg); }
        
        .button-warning { border-color: var(--warning-color); color: var(--warning-color); }
        .button-warning:hover:not(:disabled) { background-color: var(--warning-color); color: var(--body-bg); }

        .button-secondary { border-color: var(--secondary-color); color: var(--secondary-color); }
        .button-secondary:hover:not(:disabled) { background-color: var(--secondary-color); color: var(--container-bg); }


        input[type="file"], input[type="number"], input[type="range"], input[type="text"] {
            padding: 8px; 
            border: 1px solid var(--light-gray); 
            border-radius: var(--border-radius);
            font-size: 0.95rem;
            background-color: var(--body-bg); 
            color: var(--text-color); 
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
            font-family: var(--font-family);
        }
        input[type="file"] {
            border: 1px dashed var(--light-gray); 
        }
        input:focus, input[type="file"]:focus-within { 
            border-color: var(--primary-color) !important;
            box-shadow: 0 0 5px var(--primary-color) !important; 
            outline: none;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        .form-group label {
            display: block;
            margin-bottom: 8px;
            font-weight: normal;
            font-size: 1rem;
        }
        .form-group small {
            display: block;
            color: var(--secondary-color);
            font-size: 0.85rem;
            margin-top: 5px;
        }

        #gameCanvas {
            border: 2px solid var(--primary-color); 
            background-color: #1c1c1c; /* Darker gray for canvas background */
            margin: 0 auto 20px auto; 
            display: block;
            max-width: 100%;
            height: auto;
            border-radius: var(--border-radius);
        }

        #statusMessageGlobal {
            width: 100%;
            text-align: center;
            margin-bottom: 20px;
            font-weight: normal; 
            font-size: 1.05rem;
            min-height: 1.5em; 
            padding: 10px; 
            border-radius: var(--border-radius);
            box-sizing: border-box;
            border: 1px solid var(--text-color);
        }
        .status-info { border-color: var(--info-color); color: var(--info-color); background-color: rgba(0, 255, 255, 0.05);} 
        .status-error { border-color: var(--danger-color); color: var(--danger-color); background-color: rgba(255, 0, 0, 0.05);}
        .status-success { border-color: var(--success-color); color: var(--success-color); background-color: rgba(0, 255, 0, 0.05);}
        .status-warning { border-color: var(--warning-color); color: var(--warning-color); background-color: rgba(255, 255, 0, 0.05);}


        .controls-grid {
            display: flex;
            flex-wrap: wrap;
            gap: 10px; 
            align-items: center;
            justify-content: flex-start; 
        }
        .controls-grid.centered { 
             justify-content: center;
        }
        
        #scoreInputsContainer {
            display: flex;
            flex-wrap: wrap;
            gap: 8px; 
            justify-content: center; 
            margin-bottom: 20px; 
            padding: 10px;
            border: 1px dashed var(--light-gray);
        }
        #scoreInputsContainer input.score-input {
            padding: 8px;
            box-sizing: border-box;
            text-align: center;
            border: 1px solid var(--light-gray);
            border-radius: var(--border-radius);
            font-size: 1rem;
            width: calc(50% - 4px); 
            min-width: 60px;
        }
        @media (min-width: 480px) { 
            #scoreInputsContainer input.score-input {
                width: calc(33.333% - 6px); 
            }
        }
        @media (min-width: 600px) { 
            #scoreInputsContainer input.score-input {
                width: calc(20% - 7px); 
            }
        }

        .score-input.highlight {
            border-color: var(--primary-color) !important;
            box-shadow: 0 0 8px var(--primary-color) !important; 
            background-color: #223322; 
            color: #ccffcc; 
        }
        #participationError {
            color: var(--danger-color);
            margin-top: 10px;
            font-size: 0.9rem;
            text-align: center;
        }

        .info-panel {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); 
            gap: 10px 15px;
            font-size: 0.95rem; 
            border-top: 1px solid var(--light-gray);
            padding-top: 15px;
        }
        .info-panel p { margin: 5px 0; }
        .info-panel .label { font-weight: normal; color: var(--text-color); }
        .info-panel .value { color: var(--secondary-color); }
        
        .game-over-text-overlay { 
            font-weight: normal;
            color: var(--primary-color);
            text-shadow: 1px 1px 0px #000;
            text-align: center;
        }
        .game-over-reason-text { 
             color: var(--danger-color); font-weight:normal; text-align:center;
        }

        .footer {
            margin-top: 40px; 
            padding-top: 20px;
            border-top: 1px solid var(--light-gray);
            font-size: 0.85rem;
            color: var(--secondary-color);
            text-align: center;
            width: 100%;
        }
        
        .main-content-grid {
            display: grid;
            gap: 30px; 
            grid-template-columns: 1fr; 
        }

        @media (min-width: 992px) { 
            .main-content-grid {
                grid-template-columns: repeat(2, 1fr);
            }
            .game-setup-section,
            .live-game-view-section,
            .participation-section, 
            .full-width-separator { 
                grid-column: 1 / -1; 
            }
        }
        hr.full-width-separator { 
            grid-column: 1 / -1;
            border: none;
            border-top: 1px solid var(--light-gray);
            margin: 0; 
        }
    </style>
</head>
<body>
    <div class="page-container">
        <header>
            <h1>Snake // Game_of_Prompts</h1>
        </header>

        <div class="main-content-grid">
            <section class="section game-setup-section">
                <h2>> Game Setup</h2>
                <div class="form-group">
                    <label for="solverFile">1. Load Solver Module (.celaut.bee)</label>
                    <input type="file" id="solverFile" name="solverFile" accept=".celaut.bee">
                    <small>This solver will control the snake. Required to start.</small>
                </div>
                <div class="form-group">
                    <label for="seedInput">2. Game Seed (Optional)</label>
                    <input type="text" id="seedInput" name="seed" placeholder="Enter an integer seed (e.g., 12345)">
                    <small>Using a seed allows replaying the exact same game conditions (apples, initial snake).</small>
                </div>
                <div class="controls-grid centered">
                    <button id="startGameButton" class="button-primary">Start Live Game</button>
                    <button id="stopGameButton" class="button-danger" disabled>Stop Live Game</button>
                </div>
            </section>

            <section class="section live-game-view-section">
                <h2>> Live Game Matrix</h2>
                <div id="statusMessageGlobal" class="status-message">Loading system parameters...</div>
                <canvas id="gameCanvas"></canvas>
                <div id="infoPanelLive" class="info-panel" style="background-color: transparent; box-shadow:none; padding: 10px 0;">
                     <p><span class="label">SCORE (LENGTH):</span> <span id="liveScoreDisplay" class="value">1</span></p>
                     <p><span class="label">LAST_INPUT:</span> <span id="liveMoveMade" class="value">N/A</span></p>
                     <p><span class="label">SEED:</span> <span id="liveSeedDisplay" class="value">N/A</span></p>
                     </div>
            </section>

            <section id="participationDataSection" class="section participation-section" style="display:none;">
                <h2>> Participation Data Protocol</h2>
                <p>Score list for your entry. One is your actual score (final snake length), highlighted (non-editable). Regenerate dummy scores or edit others.</p>
                <div id="scoreInputsContainer">
                    <input type="number" id="scoreInput0" class="score-input" aria-label="Score 1">
                    <input type="number" id="scoreInput1" class="score-input" aria-label="Score 2">
                    <input type="number" id="scoreInput2" class="score-input" aria-label="Score 3">
                    <input type="number" id="scoreInput3" class="score-input" aria-label="Score 4">
                    <input type="number" id="scoreInput4" class="score-input" aria-label="Score 5">
                </div>
                <div class="form-group" id="indeterminismInputGroup" style="margin-top: 20px; margin-bottom: 15px; text-align: center;">
                    <label for="postGameIndeterminismIndexInput" style="display: inline-block; margin-right: 10px;">Solver Indeterminism Index:</label>
                    <input type="number" id="postGameIndeterminismIndexInput" name="postGameIndeterminismIndex" value="1" min="1" step="1" style="width: 80px; padding: 6px;" disabled>
                    <small style="display: block; margin-top: 5px;">Expected attempts to reproduce solver behavior (1 for deterministic solvers). Annotate before download.</small>
                </div>
                <div class="controls-grid centered">
                    <button id="regenerateScoresButton" class="button-warning">Regen_Dummy_Scores</button>
                    <button id="downloadParticipationDataButton" class="button-success">Download_Data_Packet</button>
                </div>
                <p id="participationError"></p>
                <p style="text-align: center; margin-top: 20px; font-size: 0.9em; color: var(--secondary-color);">
                    To use these results, load the downloaded JSON file into the corresponding smart contract interface on the Game of Prompts platform.
                </p>
            </section>
            
            <hr class="full-width-separator"> 

            <section class="section history-section">
                <h2>> Archive & Playback Unit</h2>
                <div class="form-group">
                    <label for="historyFile">Load Archive for Playback (.json)</label>
                    <input type="file" id="historyFile" accept=".json">
                </div>
                <button id="downloadLogsButton" class="button-secondary" disabled>Download Log (Last Game)</button>
                
                <h3>Playback Controls</h3>
                <div class="controls-grid replay-controls-panel" style="background-color: var(--container-bg); padding:15px; border: 1px solid var(--light-gray); border-radius: var(--border-radius); margin-top:15px;">
                    <button id="playPauseBtn" class="button-primary" disabled>Play</button>
                    <button id="prevBtn" class="button-secondary" disabled>&laquo; Prev</button>
                    <button id="nextBtn" class="button-secondary" disabled>Next &raquo;</button>
                    <div class="speed-control" style="margin-left:auto; display:flex; align-items:center; gap:8px;">
                        <label for="speedRange" style="font-size:0.9em; margin-bottom:0;">Speed:</label>
                        <input type="range" id="speedRange" min="50" max="1000" value="200" step="50" disabled style="flex-grow:1; max-width:150px;">
                        <span id="speedValue" style="font-size:0.9em; min-width:45px;">200ms</span>
                    </div>
                </div>
            </section>

            <section class="section info-summary-section">
                <h2>> Detailed Telemetry (Playback)</h2>
                <div id="infoPanelReplay" class="info-panel">
                    <p><span class="label">FRAME_COUNT:</span> <span id="frameCounter" class="value">0</span> / <span id="totalFrames" class="value">0</span></p>
                    <p><span class="label">SCORE (LENGTH):</span> <span id="replayScoreDisplay" class="value">1</span></p>
                    <p><span class="label">INPUT:</span> <span id="replayMoveMade" class="value">N/A</span></p>
                    <p><span class="label">GAME_OVER_FLAG:</span> <span id="gameOverStatus" class="value">No</span></p>
                    <p><span class="label">SEED:</span> <span id="replaySeedDisplay" class="value">N/A</span></p>
                    <div id="gameOverReasonContainer" style="display:none;" class="game-over-reason-text"> 
                    </div>
                </div>
            </section>
        </div> 

        <footer class="footer">
            BOARD_DIMENSIONS: {{ BOARD_ROWS }}r x {{ BOARD_COLS }}c. CELL_SIZE: ADAPTIVE.
            <br>CLIENT_REFRESH_RATE: ~200ms. SERVER_GAME_STEP: {{ FREQUENCY }}s.
        </footer>
    </div>

    <script>
        const canvas = document.getElementById('gameCanvas');
        const ctx = canvas.getContext('2d');
        const boardRows = {{ BOARD_ROWS }};
        const boardCols = {{ BOARD_COLS }};
        
        const mainContainerElement = document.querySelector('.page-container');
        const canvasContainerWidth = mainContainerElement.offsetWidth * 0.98; 
        
        const cellWidthDim = Math.floor(canvasContainerWidth / boardCols);
        const cellHeightDim = Math.floor((window.innerHeight * 0.30) / boardRows); 
        const cellSize = Math.max(5, Math.min(cellWidthDim, cellHeightDim, 18)); 
        
        canvas.width = boardCols * cellSize;
        canvas.height = boardRows * cellSize;

        const startGameButton = document.getElementById('startGameButton');
        const stopGameButton = document.getElementById('stopGameButton');
        const historyFileInput = document.getElementById('historyFile');
        const downloadLogsButton = document.getElementById('downloadLogsButton');
        const statusMessageGlobal = document.getElementById('statusMessageGlobal');
        
        const liveScoreDisplay = document.getElementById('liveScoreDisplay');
        const liveMoveMade = document.getElementById('liveMoveMade');
        const liveSeedDisplay = document.getElementById('liveSeedDisplay');

        const playPauseBtn = document.getElementById('playPauseBtn');
        const prevBtn = document.getElementById('prevBtn');
        const nextBtn = document.getElementById('nextBtn');
        const speedRange = document.getElementById('speedRange');
        const speedValueDisplay = document.getElementById('speedValue');
        
        const frameCounterDisplay = document.getElementById('frameCounter');
        const totalFramesDisplay = document.getElementById('totalFrames');
        const replayScoreDisplay = document.getElementById('replayScoreDisplay'); 
        const replayMoveMade = document.getElementById('replayMoveMade'); 
        const gameOverStatusDisplay = document.getElementById('gameOverStatus');
        const gameOverReasonContainer = document.getElementById('gameOverReasonContainer');
        const replaySeedDisplay = document.getElementById('replaySeedDisplay');

        const solverFileInput = document.getElementById('solverFile');
        const seedInput = document.getElementById('seedInput');

        const participationDataSection = document.getElementById('participationDataSection');
        const scoreInputsContainer = document.getElementById('scoreInputsContainer');
        const scoreInputs = [
            document.getElementById('scoreInput0'), document.getElementById('scoreInput1'),
            document.getElementById('scoreInput2'), document.getElementById('scoreInput3'),
            document.getElementById('scoreInput4')
        ];
        const regenerateScoresButton = document.getElementById('regenerateScoresButton');
        const downloadParticipationDataButton = document.getElementById('downloadParticipationDataButton');
        const participationErrorText = document.getElementById('participationError');
        const postGameIndeterminismIndexInput = document.getElementById('postGameIndeterminismIndexInput'); // New selector

        let loadedFileHistory = [];
        let currentReplayFrame = 0;
        let isReplayingFile = false;
        let replayAnimationTimeoutId;
        let replaySpeed = 200;

        let liveGameUpdateIntervalId = null;
        const LIVE_UPDATE_INTERVAL = 200; 
        let isLiveGameRunningClientSide = false;
        let gameOverPollCount = 0; 

        let js_true_score = null;
        let js_current_score_list = [];
        let js_solver_id = null;
        let js_hash_logs_hex = null;
        let js_commitment_c_hex = null;
        let js_game_seed = null; 
        const MAX_POSSIBLE_SCORE_JS = boardRows * boardCols;

        startGameButton.addEventListener('click', startLiveGame);
        stopGameButton.addEventListener('click', stopLiveGame);
        historyFileInput.addEventListener('change', loadHistoryFromFileForReplay);
        downloadLogsButton.addEventListener('click', () => window.location.href = '/download_history');
        
        playPauseBtn.addEventListener('click', toggleFileReplayPlayPause);
        prevBtn.addEventListener('click', () => showFileReplayFrame(currentReplayFrame - 1));
        nextBtn.addEventListener('click', () => showFileReplayFrame(currentReplayFrame + 1));
        speedRange.addEventListener('input', (e) => {
            replaySpeed = parseInt(e.target.value, 10);
            speedValueDisplay.textContent = `${replaySpeed}ms`;
            if (isReplayingFile && playPauseBtn.textContent === 'Pause') { 
                clearTimeout(replayAnimationTimeoutId);
                fileReplayGameLoop();
            }
        });
        
        regenerateScoresButton.addEventListener('click', () => {
            if (js_true_score !== null) {
                js_current_score_list = generateClientSideScoreList(js_true_score, 5, MAX_POSSIBLE_SCORE_JS);
                displayScoreInputs(js_current_score_list, js_true_score);
            }
        });
        
        downloadParticipationDataButton.addEventListener('click', () => {
            if (!js_solver_id || js_true_score === null || !js_hash_logs_hex || !js_commitment_c_hex || js_game_seed === undefined ) {
                alert("Essential participation data from server (Solver ID, Score, Hashes, Seed) is missing. Cannot download.");
                participationErrorText.textContent = "Error: Essential server data missing.";
                return;
            }

            let currentScoresFromInputs = [];
            for(let i=0; i < scoreInputs.length; i++){
                const inputElement = scoreInputs[i];
                const val = parseInt(inputElement.value);
                if (!isNaN(val) && val >=0) { 
                    currentScoresFromInputs.push(val);
                } else {
                    alert("One or more scores are invalid (must be non-negative numbers). Please correct them.");
                    participationErrorText.textContent = "Error: Invalid scores in fields.";
                    inputElement.focus(); 
                    return;
                }
            }
            if (!currentScoresFromInputs.includes(js_true_score)) {
                 alert("The actual score (previously highlighted) must be present in one of the score list fields.");
                 participationErrorText.textContent = "Error: Actual score not in current list.";
                 return;
            }
            if (currentScoresFromInputs.length !== 5) { 
                alert("The score list must contain 5 elements.");
                participationErrorText.textContent = "Error: List does not have 5 scores.";
                return;
            }
            js_current_score_list = currentScoresFromInputs; 

            let indeterminismIndexVal;
            try {
                indeterminismIndexVal = parseInt(postGameIndeterminismIndexInput.value);
                if (isNaN(indeterminismIndexVal) || indeterminismIndexVal < 1) {
                    alert("Solver Indeterminism Index must be an integer greater than or equal to 1.");
                    participationErrorText.textContent = "Error: Invalid Indeterminism Index.";
                    postGameIndeterminismIndexInput.focus();
                    return;
                }
            } catch (e) {
                alert("Invalid format for Solver Indeterminism Index.");
                participationErrorText.textContent = "Error: Invalid Indeterminism Index format.";
                postGameIndeterminismIndexInput.focus();
                return;
            }

            const dataToDownload = { 
                solver_id: js_solver_id,
                hash_logs_hex: js_hash_logs_hex,
                commitment_c_hex: js_commitment_c_hex,
                score_list: js_current_score_list,
                seed: js_game_seed, 
                indeterminism_index: indeterminismIndexVal 
            };

            const jsonString = JSON.stringify(dataToDownload, null, 2);
            const blob = new Blob([jsonString], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `gop_participation_${js_solver_id ? js_solver_id.substring(0,8) : 'game'}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            participationErrorText.textContent = "Data downloaded. ¡Remember to store the logs and <solver>.celaut.bee files!"; 
        });

        function setStatusMessage(message, type = 'info') {
            statusMessageGlobal.textContent = message;
            statusMessageGlobal.className = 'status-message'; 
            if (type === 'error') {
                statusMessageGlobal.classList.add('status-error');
            } else if (type === 'success') {
                statusMessageGlobal.classList.add('status-success');
            } else if (type === 'warning') {
                statusMessageGlobal.classList.add('status-warning');
            } else { 
                statusMessageGlobal.classList.add('status-info');
            }
        }

        function startGamePolling() {
            if (liveGameUpdateIntervalId === null) {
                gameOverPollCount = 0; 
                liveGameUpdateIntervalId = setInterval(fetchLiveGameState, LIVE_UPDATE_INTERVAL);
                isLiveGameRunningClientSide = true; 
                startGameButton.disabled = true;
                stopGameButton.disabled = false;
                historyFileInput.disabled = true;
                solverFileInput.disabled = true;
                seedInput.disabled = true;
                downloadLogsButton.disabled = true; 
                disableParticipationUI(); 
                participationDataSection.style.display = 'none';
            }
        }

        function stopGamePolling(messageToShow = "Polling stopped.") { 
            if (liveGameUpdateIntervalId !== null) {
                clearInterval(liveGameUpdateIntervalId);
                liveGameUpdateIntervalId = null;
            }
            isLiveGameRunningClientSide = false; 
            enableUIForIdle(); 
        }

        async function startLiveGame() {
            if (isLiveGameRunningClientSide) {
                setStatusMessage("A live game is already in progress or starting.", "info"); 
                return;
            }
            if (isReplayingFile) { 
                isReplayingFile = false;
                clearTimeout(replayAnimationTimeoutId);
                playPauseBtn.textContent = 'Play'; 
            }
            disableFileReplayControls();
            setStatusMessage('Processing solver and starting game on server...', 'info'); 
            drawGrid(); 
            
            const formData = new FormData();
            if (solverFileInput.files.length > 0) {
                formData.append('solverFile', solverFileInput.files[0]);
            } else {
                setStatusMessage('Error: You must select a solver file (.celaut.bee).', 'error'); 
                return; 
            }

            const seed = seedInput.value;
            if (seed) {
                formData.append('seed', seed);
            }
            
            js_true_score = null;
            js_current_score_list = [];
            js_solver_id = null;
            js_hash_logs_hex = null;
            js_commitment_c_hex = null;
            js_game_seed = null; 
            disableParticipationUI(); 
            participationDataSection.style.display = 'none'; 

            try {
                const response = await fetch('/start_live_game', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();
                if (data.success) {
                    setStatusMessage(data.message, 'info'); 
                    startGamePolling(); 
                } else {
                    if (data.message && data.message.toLowerCase().includes("a live game is already in progress")) { 
                        setStatusMessage("A game is already in progress on the server. Syncing...", "info"); 
                        startGamePolling(); 
                    } else {
                        setStatusMessage(`Error starting: ${data.message}`, 'error'); 
                        enableUIForIdle(); 
                    }
                }
            } catch (error) { 
                console.error("Error sending start game request:", error); 
                setStatusMessage('Connection error when trying to start the game.', 'error'); 
                enableUIForIdle();
            }
        }

        async function stopLiveGame() {
            setStatusMessage('Sending signal to stop live game...', 'info'); 
            try {
                await fetch('/stop_live_game', {method: 'POST'});
            } catch (error) {
                 console.error("Error stopping game:", error); 
                 setStatusMessage("Connection error when stopping game.", 'error'); 
            }
        }

        async function fetchLiveGameState() {
            try {
                const response = await fetch('/get_live_game_state');
                if (!response.ok) {
                    console.warn(`HTTP error fetching state: ${response.status} ${response.statusText}`); 
                    let errorMsg = `Warning: Problem fetching state (${response.status}). Polling stopped.`; 
                    setStatusMessage(errorMsg, 'error');
                    stopGamePolling(errorMsg); 
                    disableParticipationUI(); 
                    participationDataSection.style.display = 'none';
                    return;
                }
                const state = await response.json();

                liveSeedDisplay.textContent = state.seed !== null && state.seed !== undefined ? state.seed : 'N/A';

                if (state.solver_is_starting) {
                    setStatusMessage(state.message || "Solver is initializing, game will start shortly...", 'info');
                    drawUniversalFrame(state);
                    startGameButton.disabled = true;
                    stopGameButton.disabled = false;
                    historyFileInput.disabled = true;
                    solverFileInput.disabled = true;
                    seedInput.disabled = true;
                    downloadLogsButton.disabled = true;
                    participationDataSection.style.display = 'none';
                    disableParticipationUI();
                    return; 
                }
                
                // Removed the old: if (!state.game_active && statusMessageGlobal.textContent.includes("Processing solver")) { ... return; }

                if (!state.game_active && !state.game_over && state.message && state.message.includes("No active game or not started. Ready to begin.")) { 
                    setStatusMessage(state.message, 'info');
                    drawUniversalFrame(state); 
                    stopGamePolling(state.message); 
                    downloadLogsButton.disabled = true;
                    disableParticipationUI();
                    participationDataSection.style.display = 'none';
                    return;
                }
                
                drawUniversalFrame(state); 

                if (state.participation_data_ready === true) {
                    participationDataSection.style.display = 'block';
                    postGameIndeterminismIndexInput.value = "1"; 
                    postGameIndeterminismIndexInput.disabled = false; 
                    participationErrorText.textContent = '';
                    js_true_score = parseInt(state.true_score);
                    js_solver_id = state.solver_id;
                    js_hash_logs_hex = state.hash_logs_hex;
                    js_commitment_c_hex = state.commitment_c_hex;
                    js_game_seed = state.seed; 
                    
                    if ((!js_current_score_list || js_current_score_list.length === 0) && state.score_list && state.score_list.length > 0) {
                         js_current_score_list = state.score_list.map(s => parseInt(s));
                    } else if ((!js_current_score_list || js_current_score_list.length === 0) && js_true_score !== null) { 
                         js_current_score_list = generateClientSideScoreList(js_true_score, 5, MAX_POSSIBLE_SCORE_JS);
                    }
                    displayScoreInputs(js_current_score_list, js_true_score);
                    regenerateScoresButton.disabled = false;
                    downloadParticipationDataButton.disabled = false;
                } else if (state.game_over && state.participation_data_error) {
                    participationDataSection.style.display = 'block'; 
                    participationErrorText.textContent = `Participation data error: ${state.participation_data_error}`; 
                    disableParticipationUI(); 
                } else if (state.game_over) { 
                    participationDataSection.style.display = 'block'; 
                    participationErrorText.textContent = state.participation_data_error || "Generating participation data..."; 
                    disableParticipationUI(); 
                } else { 
                    participationDataSection.style.display = 'none';
                    disableParticipationUI();
                }

                if (state.game_over) {
                    const reason = state.game_over_reason || 'Finished'; 
                    let gameStatusMsg = `Game over: ${reason}.`; 
                    
                    if (state.participation_data_ready === true || (state.participation_data_error && !state.participation_data_error.includes("pending"))) { 
                        if(state.participation_data_ready) gameStatusMsg += " Participation data ready."; 
                        else gameStatusMsg += ` ${state.participation_data_error || 'Error in participation data.'}`; 
                        setStatusMessage(gameStatusMsg, state.participation_data_ready ? 'success' : 'error');
                        stopGamePolling(gameStatusMsg); 
                        gameOverPollCount = 0; 
                    } else { 
                         gameStatusMsg += " Waiting for participation data..."; 
                         setStatusMessage(gameStatusMsg, 'info');
                         gameOverPollCount++;
                         const MAX_PARTICIPATION_POLLS = 25; 
                         if (gameOverPollCount > MAX_PARTICIPATION_POLLS) { 
                             let finalMsg = `Game over: ${reason}. Timeout waiting for participation data.`; 
                             setStatusMessage(finalMsg, 'warning');
                             stopGamePolling(finalMsg); 
                             gameOverPollCount = 0; 
                             if (!state.participation_data_ready) { 
                                participationDataSection.style.display = 'block';
                                participationErrorText.textContent = "Could not retrieve participation data from server after waiting."; 
                                disableParticipationUI();
                             }
                         }
                    }
                    downloadLogsButton.disabled = (state.game_history_globals_length_DEBUG === 0);
                }
                else if (!state.game_active && isLiveGameRunningClientSide) { 
                    setStatusMessage("Game on server is not active or has been stopped.", 'warning'); 
                    stopGamePolling("Game on server is not active or has been stopped."); 
                    downloadLogsButton.disabled = (state.game_history_globals_length_DEBUG === 0);
                }
                else if (state.game_active && !isLiveGameRunningClientSide) { 
                    setStatusMessage("Synced with existing live game. Updating...", 'info'); 
                    startGamePolling(); 
                }
                else if (state.game_active && isLiveGameRunningClientSide) {
                    setStatusMessage("Live game in progress...", 'info'); 
                    liveScoreDisplay.textContent = state.score || '1';
                    liveMoveMade.textContent = state.move_made || 'N/A';
                }

            } catch (error) { 
                console.error('Error fetching/processing live game state:', error); 
                setStatusMessage('Connection error or invalid data from server. Polling stopped.', 'error'); 
                stopGamePolling('Connection error or invalid data from server. Polling stopped.'); 
                disableParticipationUI();
                participationDataSection.style.display = 'none';
            }
        }
        
        function disableParticipationUI() {
            regenerateScoresButton.disabled = true;
            downloadParticipationDataButton.disabled = true;
            postGameIndeterminismIndexInput.disabled = true; 
            scoreInputs.forEach(input => { 
                input.value = ''; 
                input.classList.remove('highlight');
                input.readOnly = false; 
            });
        }

        function loadHistoryFromFileForReplay(event) {
            if (isLiveGameRunningClientSide) {
                alert("Cannot load history while following a live game."); 
                event.target.value = null; 
                return;
            }
            const file = event.target.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    try {
                        const parsedHistory = JSON.parse(e.target.result);
                        if (Array.isArray(parsedHistory) && parsedHistory.length > 0) {
                            loadedFileHistory = parsedHistory;
                            setStatusMessage(`History "${file.name}" loaded. Ready to replay.`, 'success'); 
                            resetUIForFileReplay();
                            downloadLogsButton.disabled = true; 
                            disableParticipationUI(); 
                            participationDataSection.style.display = 'none';
                        } else {
                            alert("Empty history or invalid format (must be an array of frames)."); 
                            resetAfterFileLoadAttempt();
                        }
                    } catch (err) {
                        alert("Error parsing JSON: " + err.message); 
                        resetAfterFileLoadAttempt();
                    }
                };
                reader.readAsText(file);
            }
        }

        function resetAfterFileLoadAttempt() {
            loadedFileHistory = [];
            resetUIForFileReplay(); 
            if(historyFileInput) historyFileInput.value = null; 
        }

        function resetUIForFileReplay() {
            currentReplayFrame = 0;
            isReplayingFile = false;
            clearTimeout(replayAnimationTimeoutId);
            playPauseBtn.textContent = 'Play'; 
            if (loadedFileHistory && loadedFileHistory.length > 0) {
                totalFramesDisplay.textContent = loadedFileHistory.length -1; 
                enableFileReplayControls();
                showFileReplayFrame(0); 
            } else {
                disableFileReplayControls();
                drawGrid(); 
                frameCounterDisplay.textContent = "0"; 
                totalFramesDisplay.textContent = "0";
            }
        }

        function enableFileReplayControls() {
            playPauseBtn.disabled = false;
            prevBtn.disabled = (currentReplayFrame <= 0);
            nextBtn.disabled = (currentReplayFrame >= loadedFileHistory.length - 1); 
            speedRange.disabled = false;
        }

        function disableFileReplayControls() {
            playPauseBtn.disabled = true;
            prevBtn.disabled = true;
            nextBtn.disabled = true;
            speedRange.disabled = true;
            playPauseBtn.textContent = 'Play'; 
            isReplayingFile = false;
            clearTimeout(replayAnimationTimeoutId);
        }

        function enableUIForIdle() {
            startGameButton.disabled = false;
            stopGameButton.disabled = true;
            historyFileInput.disabled = false;
            solverFileInput.disabled = false;
            seedInput.disabled = false;
        }

        function showFileReplayFrame(frameIndex) {
            if (!loadedFileHistory || loadedFileHistory.length === 0) {
                drawGrid(); return;
            }
            currentReplayFrame = Math.max(0, Math.min(frameIndex, loadedFileHistory.length - 1));
            const frameData = loadedFileHistory[currentReplayFrame];
            drawUniversalFrame(frameData, currentReplayFrame, true); 
            prevBtn.disabled = currentReplayFrame <= 0;
            nextBtn.disabled = currentReplayFrame >= loadedFileHistory.length - 1;
        }

        function fileReplayGameLoop() {
            if (!isReplayingFile) return; 
            const nextFrameToShow = currentReplayFrame + 1;
            if (nextFrameToShow < loadedFileHistory.length) {
                showFileReplayFrame(nextFrameToShow);
                if (!(loadedFileHistory[currentReplayFrame] && loadedFileHistory[currentReplayFrame].game_over)) {
                     replayAnimationTimeoutId = setTimeout(fileReplayGameLoop, replaySpeed);
                } else { 
                    isReplayingFile = false;
                    playPauseBtn.textContent = 'Play'; 
                }
            } else { 
                isReplayingFile = false;
                playPauseBtn.textContent = 'Play'; 
            }
        }

        function toggleFileReplayPlayPause() {
            if (!loadedFileHistory || loadedFileHistory.length === 0) return;
            isReplayingFile = !isReplayingFile;
            playPauseBtn.textContent = isReplayingFile ? 'Pause' : 'Play'; 
            if (isReplayingFile) {
                if (currentReplayFrame >= loadedFileHistory.length - 1 ||
                    (loadedFileHistory[currentReplayFrame] && loadedFileHistory[currentReplayFrame].game_over)) {
                    showFileReplayFrame(0); 
                    if (!(loadedFileHistory[0] && loadedFileHistory[0].game_over)) {
                        replayAnimationTimeoutId = setTimeout(fileReplayGameLoop, replaySpeed);
                    } else { 
                        isReplayingFile = false; 
                        playPauseBtn.textContent = 'Play'; 
                    }
                } else { 
                    fileReplayGameLoop(); 
                }
            } else { 
                clearTimeout(replayAnimationTimeoutId);
            }
        }
        
        function drawUniversalFrame(frameData, replayFrameNumber = null, isReplay = false) {
            drawGrid();
            const snakeColor = '#00dd00'; 
            const headColor = '#00ff00'; 
            const appleColor = '#ff4100'; 
            
            const currentScoreTarget = isReplay ? replayScoreDisplay : liveScoreDisplay;
            const currentMoveTarget = isReplay ? replayMoveMade : liveMoveMade;
            const currentSeedTarget = isReplay ? replaySeedDisplay : liveSeedDisplay;

            if (frameData.snake && Array.isArray(frameData.snake) && frameData.snake.length > 0) {
                frameData.snake.forEach((segment, index) => {
                    if (Array.isArray(segment) && segment.length === 2) {
                       drawCell(segment[0], segment[1], index === 0 ? headColor : snakeColor, index === 0);
                    }
                });
                currentScoreTarget.textContent = frameData.score !== undefined ? frameData.score : (frameData.snake.length || 1);
            } else {
                currentScoreTarget.textContent = '1'; 
            }

            if (frameData.apple && Array.isArray(frameData.apple) && frameData.apple.length === 2) { 
                drawCell(frameData.apple[0], frameData.apple[1], appleColor); 
            }

            currentMoveTarget.textContent = frameData.move_made || 'N/A'; 
            currentSeedTarget.textContent = frameData.seed !== undefined && frameData.seed !== null ? frameData.seed : 'N/A';
            
            if(isReplay) { 
                gameOverStatusDisplay.textContent = frameData.game_over ? 'Yes' : 'No'; 
            }

            if (frameData.game_over) {
                const reasonText = frameData.game_over_reason || 'Unknown reason'; 
                if(isReplay) {
                    gameOverReasonContainer.textContent = `Reason: ${reasonText}`; 
                    gameOverReasonContainer.style.display = 'block'; 
                }
                
                ctx.fillStyle = 'rgba(0, 0, 0, 0.75)'; 
                ctx.fillRect(0, canvas.height / 2 - Math.floor(cellSize * 2), canvas.width, Math.floor(cellSize * 4));
                ctx.font = `bold ${Math.max(16, Math.floor(cellSize * 1.5))}px var(--font-family)`;
                ctx.fillStyle = '#ff0000'; 
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText('GAME OVER', canvas.width / 2, canvas.height / 2);

                if (frameData.move_made && frameData.snake && frameData.snake.length > 0) {
                    const head = frameData.snake[0];
                    let intended_new_head = [...head]; 
                    const failed_move = frameData.move_made; 
                    if (failed_move === 'UP') intended_new_head[0]--;
                    else if (failed_move === 'DOWN') intended_new_head[0]++;
                    else if (failed_move === 'LEFT') intended_new_head[1]--;
                    else if (failed_move === 'RIGHT') intended_new_head[1]++;
                    
                    const reason_lower = (frameData.game_over_reason || "").toLowerCase();
                    if (reason_lower.includes('collision') || reason_lower.includes('wall')) { 
                        if (intended_new_head[0] < 0 || intended_new_head[0] >= boardRows ||
                            intended_new_head[1] < 0 || intended_new_head[1] >= boardCols) {
                             drawCollisionIndicator(head[0], head[1], true); 
                        } else {
                             drawCollisionIndicator(intended_new_head[0], intended_new_head[1]);
                        }
                    }
                }
            } else if (isReplay) { 
                gameOverReasonContainer.style.display = 'none'; 
            }

            if (replayFrameNumber !== null && isReplay) { 
                frameCounterDisplay.textContent = replayFrameNumber;
                totalFramesDisplay.textContent = (loadedFileHistory.length > 0) ? loadedFileHistory.length -1 : 0;
            } else if (isReplay) { 
                 frameCounterDisplay.textContent = "0";
                 totalFramesDisplay.textContent = (loadedFileHistory.length > 0) ? loadedFileHistory.length -1 : 0;
            }
        }


        function drawGrid() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.strokeStyle = '#444'; 
            ctx.lineWidth = 0.5; 
            for (let r = 0; r < boardRows; r++) {
                for (let c = 0; c < boardCols; c++) {
                    ctx.strokeRect(c * cellSize, r * cellSize, cellSize, cellSize);
                }
            }
        }

        function drawCell(row, col, color, isHead = false) {
            ctx.fillStyle = color;
            ctx.fillRect(col * cellSize, row * cellSize, cellSize, cellSize); 
        }

        function drawCollisionIndicator(row, col, onHead = false) {
            const x = col * cellSize;
            const y = row * cellSize;
            ctx.fillStyle = 'rgba(255, 0, 0, 0.7)'; 
            ctx.fillRect(x, y, cellSize, cellSize); 
            ctx.font = `bold ${Math.floor(cellSize*0.9)}px var(--font-family)`; 
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillStyle = '#000'; 
            ctx.fillText('X', x + cellSize / 2, y + cellSize / 2 + 1); 
        }

        function displayScoreInputs(list, trueScore) {
            if (!list || list.length === 0) { 
                if (js_true_score !== null) {
                    list = generateClientSideScoreList(js_true_score, 5, MAX_POSSIBLE_SCORE_JS);
                    js_current_score_list = list; 
                } else { 
                    scoreInputs.forEach(input => { 
                        input.value = ''; 
                        input.classList.remove('highlight');
                        input.readOnly = false; 
                    });
                    return;
                }
            }
            let displayList = [...list];
            while(displayList.length < 5 && js_true_score !== null) { 
                displayList.push(Math.floor(Math.random() * MAX_POSSIBLE_SCORE_JS) + 1);
            }
            displayList = displayList.slice(0,5);

            displayList.forEach((score, index) => {
                if (scoreInputs[index]) {
                    scoreInputs[index].value = parseInt(score); 
                    if (parseInt(score) === parseInt(trueScore)) {
                        scoreInputs[index].classList.add('highlight');
                        scoreInputs[index].readOnly = true; 
                    } else {
                        scoreInputs[index].classList.remove('highlight');
                        scoreInputs[index].readOnly = false; 
                    }
                }
            });
        }

        function generateClientSideScoreList(trueScore, size = 5, maxScore = MAX_POSSIBLE_SCORE_JS) {
            let scoresSet = new Set();
            scoresSet.add(parseInt(trueScore));
            const power_bias = 2.5; 
            let attempts = 0;
            const minScorePossible = 1; 
            while (scoresSet.size < size && attempts < size * 30) { 
                let value_range = maxScore - minScorePossible;
                let dummy_score = minScorePossible;
                if (value_range >= 0) { 
                    let skew_factor = Math.random() ** power_bias; 
                    dummy_score = minScorePossible + Math.floor(skew_factor * (value_range + 1));
                    dummy_score = Math.min(maxScore, dummy_score); 
                    dummy_score = Math.max(minScorePossible, dummy_score); 
                }
                scoresSet.add(dummy_score);
                attempts++;
            }
            
            let resultList = Array.from(scoresSet);
            let idx_filler_fallback = 0;
            while (resultList.length < size) { 
                 let filler_score = Math.floor(Math.random() * (maxScore - minScorePossible + 1)) + minScorePossible; 
                 if (!resultList.includes(filler_score)) { resultList.push(filler_score); }
                 else { 
                    idx_filler_fallback++;
                    let alt_filler = (parseInt(trueScore) + idx_filler_fallback);
                    if (alt_filler > maxScore) { alt_filler = minScorePossible + (alt_filler % (maxScore - minScorePossible +1)) ; }
                     else if (alt_filler < minScorePossible) { alt_filler = minScorePossible; } 
                    
                    if (!resultList.includes(alt_filler)) resultList.push(alt_filler);
                    else if (resultList.length < size) resultList.push(Math.floor(Math.random() * (maxScore - minScorePossible + 1)) + minScorePossible); 
                 }
            }
            
            let finalOutputList = [];
            if(resultList.includes(parseInt(trueScore))){
                finalOutputList.push(parseInt(trueScore));
                resultList = resultList.filter(s => s !== parseInt(trueScore));
            } else { 
                finalOutputList.push(parseInt(trueScore));
            }

            for (let i = resultList.length - 1; i > 0; i--) { 
                const j = Math.floor(Math.random() * (i + 1)); 
                [resultList[i], resultList[j]] = [resultList[j], resultList[i]];
            }

            for(let s of resultList) {
                if(finalOutputList.length < size) finalOutputList.push(s);
            }
            
            idx_filler_fallback = 0;
            while(finalOutputList.length < size) {
                 let val = Math.floor(Math.random() * (maxScore - minScorePossible + 1)) + minScorePossible; 
                 if(!finalOutputList.includes(val) || idx_filler_fallback > 10) { 
                    finalOutputList.push(val);
                 }
                 idx_filler_fallback++;
            }

            for (let i = finalOutputList.length - 1; i > 0; i--) {
                const j = Math.floor(Math.random() * (i + 1)); 
                [finalOutputList[i], finalOutputList[j]] = [finalOutputList[j], finalOutputList[i]];
            }
            return finalOutputList.slice(0, size);
        }


        function initializePage() {
            drawGrid();
            enableUIForIdle(); 
            disableFileReplayControls();
            speedValueDisplay.textContent = `${replaySpeed}ms`;
            liveScoreDisplay.textContent = '1'; 
            liveMoveMade.textContent = 'N/A';
            liveSeedDisplay.textContent = 'N/A';
            replayScoreDisplay.textContent = '1';
            replayMoveMade.textContent = 'N/A';
            replaySeedDisplay.textContent = 'N/A';
            gameOverStatusDisplay.textContent = 'No';
            gameOverReasonContainer.style.display = 'none';
            frameCounterDisplay.textContent = "0";
            totalFramesDisplay.textContent = "0";
            
            disableParticipationUI(); 
            participationDataSection.style.display = 'none'; 
            
            setStatusMessage("Ready to start or load history.", 'info'); 
            fetchLiveGameState(); 
        }

        initializePage();

    </script>
</body>
</html>
"""

if __name__ == '__main__':
    logger.info(f"Snake Game Server (Live View) started at http://localhost:5000 or http://<host_ip>:5000")
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)