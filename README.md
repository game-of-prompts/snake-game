# Snake Game for Game of Prompts (GoP)

This repository contains an implementation of the classic Snake game, designed to function as a **Game Service** within the Game of Prompts (GoP) platform. It allows Solver Services (bots or AIs) to compete to achieve the highest possible score.

## General Description

The service implements the Snake game where the objective is to control a snake to eat apples, growing in length with each apple consumed. The game ends if the snake collides with the board's walls or with itself.

As a GoP Game Service, this implementation is designed to:
1.  Receive a **Solver Service** provided by a player.
2.  Run a game of Snake where the Solver Service makes the movement decisions.
3.  Evaluate the Solver's performance based on the final score (length of the snake).
4.  Generate the necessary data for the player to register their game on the Ergo blockchain through the GoP platform.

## Game Operation

### Interaction with Solver Services
* When a game starts, this Game Service loads a **Solver Service** (a `.celaut.bee` file) provided by the player.
* The Game Service manages the game state (snake's position, apple, score) and, at each game step, sends the current state to the Solver Service.
* Communication with the solver is done via an HTTP API. The Game Service sends a POST request to the solver's `/move` endpoint with the current game state in JSON format:
    ```json
    {
        "snake": [[y,x], [y,x], ...], // Coordinates of the snake segments (head first)
        "apple": [y,x],             // Apple coordinates
        "board_rows": 20,
        "board_cols": 40
    }
    ```
* The Solver Service must respond with a move in JSON format:
    ```json
    {
        "move": "UP" // Options: "UP", "DOWN", "LEFT", "RIGHT"
    }
    ```
* The game progresses according to the received move. If the solver does not respond in time, returns an error, or an invalid move, the game ends.

### Game Parameters
* **Board Size:** Configured by default to 20 rows by 40 columns.
* **Seed:** The game uses a seed (an integer or a string) for random number generation (initial position of the snake and apples). This ensures that a game can be **reproducible** if the same seed and the same solver are used. The seed can be provided by the user when starting the game.

### Scoring
* The score is equal to the length of the snake. It increases by 1 each time the snake eats an apple.

## Integration with Game of Prompts

At the end of each game (whether by victory, defeat, or solver error), this Game Service generates a set of data that the player can use to participate in a GoP competition. This data includes:

* **`solverId`**: The unique identifier of the Solver Service that played the game.
* **`true_score`**: The final score (length of the snake) obtained in the game.
* **`hash_logs_hex`**: A Blake2b256 hash of the complete game history (sequence of states and moves), ensuring the integrity of the logs.
* **`commitment_c_hex`**: The cryptographic commitment that links `solverId`, `true_score`, `hash_logs_hex`, and the Game Service's secret (`SECRET_S_HEX`). This is the fundamental data that is registered on-chain.
* **`score_list`**: A list of scores (including the `true_score` and several decoy scores) to obfuscate the real score in the `ParticipationBox` until the secret is revealed.
* **`seed`**: The seed used for that specific game, allowing its reproducibility.

This data can be downloaded from the game's web interface in a standardized JSON format.

## Game Service Web Interface

This service includes an interactive web interface built with Flask that allows:
* **Configure and Start a Game:**
    * Upload a `.celaut.bee` file corresponding to the Solver Service.
    * Optionally, specify a seed for the game.
    * Start the game execution, where the Game Service will call the Solver Service.
* **Live Visualization:** Watch the Snake game in real-time on an HTML canvas. The score, last move, and current seed are displayed.
* **Stop the Game:** Interrupt an ongoing game.
* **GoP Participation Data:**
    * Once the game is finished, the interface displays the generated participation data.
    * Allows the user to review the `score_list` (with the real score highlighted) and edit the decoy scores.
    * Allows annotating an "indeterminism index" for the solver.
    * Download the JSON data package ready to be used on the GoP platform to register the participation on-chain.
* **History and Playback:**
    * Download the complete history of the last game played (detailed logs).
    * Upload a JSON history file (previously downloaded) to visually replay a past game, with speed, pause, and frame-by-frame advance controls.

## Solvers

This section describes examples of Solver Services that can interact with this Snake Game Service.

### Basic Solver

This is an example of a very simple solver for the Snake game. Its strategy is to move directly towards the apple, prioritizing the axis (vertical or horizontal) where the distance is greater. It does not implement any logic to avoid collisions with walls or its own body, so its performance will be limited.

### LLM-based Solver (currently not working!)

This solver is an advanced example designed to use a Large Language Model (LLM) for decision-making. Unlike a direct connection to a public API, this solver operates entirely within the Celaut ecosystem: it requests an Ollama service (which must be available on the Celaut network with a known service hash) as a dependency and communicates with that Celaut service instance to obtain moves.

**Operation:**
1.  **Receiving Game State:** Similar to the basic solver, it receives the current game state (snake, apple, board dimensions) from the Snake Game Service.
2.  **Prompt Formulation:** It constructs a detailed and task-specific prompt, describing the game state and rules, optimized for interpretation by an LLM.
3.  **Requesting Ollama Service as a Dependency in Celaut:**
    * It uses Celaut's `node_controller` to request an instance of a pre-packaged Ollama service available on the Celaut network. This is done using a predefined service hash (`OLLAMA_SERVICE_HASH_HEX` in the code, which must be replaced with the actual hash of the Ollama service in Celaut).
    * Once the URI of the Ollama service instance on the Celaut network is obtained, the solver sends the prompt to it.
4.  **Interaction with the Ollama Instance in Celaut:**
    * Sends the prompt to the `/api/generate` endpoint of the obtained Ollama service instance.
    * By default, it uses the model within that Ollama service.
5.  **Response Processing and Fallback:**
    * It attempts to extract a valid move ('UP', 'DOWN', 'LEFT', 'RIGHT') from the textual response generated by the LLM via the Ollama service.
    * If communication with the Ollama service fails, it does not respond in time, or the response is not a valid move, the solver resorts to a simple fallback strategy (such as choosing a valid move at random).

This approach demonstrates a deeper integration with Celaut, where services can depend on other services (like an LLM) within the same decentralized network. The quality of the moves will depend on the LLM model used in the Ollama service and the effectiveness of the prompt.
