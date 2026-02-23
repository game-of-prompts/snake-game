#!/usr/bin/env python3.11

from flask import Flask, request, jsonify, abort
import logging, os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app.log'),
    filemode='w'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/move', methods=['POST'])
def get_move():
    data = request.json

    logger.info(f"New state {data}")

    if not data or 'snake' not in data or 'apple' not in data:
        _msg = "Missing 'snake' or 'apple' data in the request."
        logger.info(_msg)
        abort(400, description=_msg)

    snake = data['snake']  # List of [row, column], snake[0] is the head
    apple = data['apple']  # [row, column]

    if not snake or not isinstance(snake, list) or not all(isinstance(pos, list) and len(pos) == 2 for pos in snake):
        _msg = "Invalid format for 'snake'. It must be a non-empty list of [row, column] positions."
        logger.info(_msg)
        abort(400, description=_msg)
    
    if not isinstance(apple, list) or len(apple) != 2 or not all(isinstance(coord, int) for coord in apple):
        _msg = "Invalid format for 'apple'. It must be a list of two integers [row, column]."
        logger.info(_msg)
        abort(400, description=_msg)

    head = snake[0]
    delta_row = apple[0] - head[0]
    delta_col = apple[1] - head[1]

    # Prioritize the direction with the greatest distance
    if abs(delta_row) > abs(delta_col):
        if delta_row < 0:
            move = 'UP'
        else:
            move = 'DOWN'
    elif abs(delta_col) > abs(delta_row): # Move horizontally if the horizontal distance is greater
        if delta_col < 0:
            move = 'LEFT'
        else:
            move = 'RIGHT'
    else: # Equal distances (or both zero, although the head shouldn't be on the apple here)
          # A default priority can be chosen, for example, horizontal or vertical
          # Or maintain the original logic if delta_row == 0 and delta_col != 0 or vice versa
        if delta_col != 0 : # Prioritize horizontal movement if possible
            if delta_col < 0:
                move = 'LEFT'
            else:
                move = 'RIGHT'
        elif delta_row !=0: # Then vertical if possible
            if delta_row < 0:
                move = 'UP'
            else:
                move = 'DOWN'
        else: # The head is on the apple (this shouldn't be queried to the solver)
              # or it's the only point, and there's no delta.
              # In a real game, this would mean the apple was eaten.
              # For safety, we choose a default move if we get here.
            move = 'RIGHT' # Default move if there's no clear priority

    logger.info(f"Movement: {move}")
    return jsonify({'move': move})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
