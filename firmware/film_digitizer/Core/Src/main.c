/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Motor Control via USART1
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "usart.h"
#include "gpio.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
void DWT_Init(void);
void delay_us(uint32_t us);
void Process_Command(char* buffer, UART_HandleTypeDef *huart);

/* Private variables ---------------------------------------------------------*/
/* USER CODE BEGIN PV */
// UART 1 Variables
uint8_t rx1_byte;
char rx1_buffer[64];
uint8_t rx1_index = 0;

// Shared Logic Variables
char active_cmd[64];
volatile uint8_t cmd_ready = 0;
volatile uint8_t stop_flag = 0;
UART_HandleTypeDef *active_huart = NULL;

typedef enum {
    MOTOR_IDLE,
    MOTOR_JOG_FWD,
    MOTOR_JOG_REV,
    MOTOR_BUSY_API
} MotorState_t;

volatile MotorState_t motor_state = MOTOR_IDLE;

const uint32_t RAMP_START_DELAY = 800;
const uint32_t RAMP_MIN_DELAY = 100;
/* USER CODE END PV */

/**
  * @brief  The application entry point.
  */
int main(void)
{
  /* MCU Configuration */
  HAL_Init();
  SystemClock_Config();

  /* Initialize Peripherals */
  MX_GPIO_Init();
  MX_USART1_UART_Init(); // Changed to USART1

  /* USER CODE BEGIN 2 */
  DWT_Init();

  // Start listening only on USART1
  HAL_UART_Receive_IT(&huart1, &rx1_byte, 1);

  uint32_t current_jog_delay = RAMP_START_DELAY;
  uint32_t ramp_counter = 0;
  uint32_t last_heartbeat = 0;

  char boot_msg[] = "\r\n--- Motor Interface Ready (USART1) ---\r\n"
                    "Use WASD to Jog, $MX:steps; for API, Q to stop.\r\n";
  HAL_UART_Transmit(&huart1, (uint8_t*)boot_msg, strlen(boot_msg), 100);
  /* USER CODE END 2 */

  /* Infinite loop */
  while (1)
  {
    // LED logic: Solid if motor active, Blinking if idle (LD2 usually PA5)
    if (motor_state != MOTOR_IDLE)
    {
        HAL_GPIO_WritePin(GPIOA, GPIO_PIN_5, GPIO_PIN_SET);
    }
    else
    {
        if (HAL_GetTick() - last_heartbeat > 500) {
            HAL_GPIO_TogglePin(GPIOA, GPIO_PIN_5);
            last_heartbeat = HAL_GetTick();
        }
    }

    // 1. Handle Precise API Movements
    if (cmd_ready && active_huart != NULL)
    {
      motor_state = MOTOR_BUSY_API;
      Process_Command(active_cmd, active_huart);
      motor_state = MOTOR_IDLE;
      cmd_ready = 0;
    }

    // 2. Ramped Toggle Jogging Logic
    if (motor_state == MOTOR_JOG_FWD || motor_state == MOTOR_JOG_REV)
    {
      HAL_GPIO_WritePin(DIR_X_PIN_GPIO_Port, DIR_X_PIN_Pin,
                       (motor_state == MOTOR_JOG_FWD) ? GPIO_PIN_RESET : GPIO_PIN_SET);

      HAL_GPIO_WritePin(STEP_X_PIN_GPIO_Port, STEP_X_PIN_Pin, GPIO_PIN_SET);
      delay_us(current_jog_delay);
      HAL_GPIO_WritePin(STEP_X_PIN_GPIO_Port, STEP_X_PIN_Pin, GPIO_PIN_RESET);
      delay_us(current_jog_delay);

      if (current_jog_delay > RAMP_MIN_DELAY) {
          ramp_counter++;
          if (ramp_counter >= 2) {
              current_jog_delay--;
              ramp_counter = 0;
          }
      }
    }
    else
    {
      current_jog_delay = RAMP_START_DELAY;
      ramp_counter = 0;
    }
  }
}

/**
  * @brief UART Rx Callback for USART1
  */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == USART1) { // Changed to Instance check for USART1
    if (rx1_byte == 'q') {
      stop_flag = 1;
      motor_state = MOTOR_IDLE;
      HAL_UART_Transmit(huart, (uint8_t*)"\r\nSTOPPED\r\n", 11, 10);
    }
    else if (rx1_byte == 'w' || rx1_byte == 'd') {
      stop_flag = 0;
      motor_state = MOTOR_JOG_FWD;
      HAL_UART_Transmit(huart, (uint8_t*)"\r\nRunning FWD\r\n", 15, 10);
    }
    else if (rx1_byte == 's' || rx1_byte == 'a') {
      stop_flag = 0;
      motor_state = MOTOR_JOG_REV;
      HAL_UART_Transmit(huart, (uint8_t*)"\r\nRunning REV\r\n", 15, 10);
    }
    else {
      if (rx1_byte == ';') {
        rx1_buffer[rx1_index] = '\0';
        strcpy(active_cmd, rx1_buffer);
        active_huart = huart;
        cmd_ready = 1;
        rx1_index = 0;
      }
      else if (rx1_byte == '$') {
        rx1_index = 0;
      }
      else if (rx1_index < 63) {
        rx1_buffer[rx1_index++] = rx1_byte;
      }
    }

    // Echo character back and re-enable interrupt
    HAL_UART_Transmit(huart, &rx1_byte, 1, 10);
    HAL_UART_Receive_IT(huart, &rx1_byte, 1);
  }
}

/**
  * @brief API Movement Processing
  */
void Process_Command(char* buffer, UART_HandleTypeDef *huart)
{
  if (strncmp(buffer, "MX:", 3) == 0)
  {
    int32_t steps = atoi(&buffer[3]);
    uint32_t speed_delay = RAMP_START_DELAY;
    stop_flag = 0;

    HAL_GPIO_WritePin(DIR_X_PIN_GPIO_Port, DIR_X_PIN_Pin, (steps > 0) ? GPIO_PIN_RESET : GPIO_PIN_SET);
    steps = (steps < 0) ? -steps : steps;

    for (int32_t i = 0; i < steps; i++)
    {
      if (stop_flag) break;
      HAL_GPIO_WritePin(STEP_X_PIN_GPIO_Port, STEP_X_PIN_Pin, GPIO_PIN_SET);
      delay_us(speed_delay);
      HAL_GPIO_WritePin(STEP_X_PIN_GPIO_Port, STEP_X_PIN_Pin, GPIO_PIN_RESET);
      delay_us(speed_delay);
      if (speed_delay > RAMP_MIN_DELAY && i % 2 == 0) speed_delay--;
    }

    char msg[128];
    sprintf(msg, "\r\n%s: %s\r\n", stop_flag ? "STOPPED" : "OK", buffer);
    HAL_UART_Transmit(huart, (uint8_t*)msg, strlen(msg), 100);
    stop_flag = 0;
  }
}

/**
  * @brief DWT Initialization - Robust Sequence
  */
void DWT_Init(void) {
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

/**
  * @brief Microsecond delay using CPU Cycle Counter
  */
void delay_us(uint32_t us) {
  uint32_t startTick = DWT->CYCCNT;
  uint32_t delayTicks = us * (SystemCoreClock / 1000000);
  while (DWT->CYCCNT - startTick < delayTicks);
}

/**
  * @brief UART Error Callback
  */
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1) {
        HAL_UART_Receive_IT(&huart1, &rx1_byte, 1);
    }
}

void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE2);

  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = 16;
  RCC_OscInitStruct.PLL.PLLN = 336;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV4;
  RCC_OscInitStruct.PLL.PLLQ = 7;
  HAL_RCC_OscConfig(&RCC_OscInitStruct);

  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK|RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;
  HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2);
}

void Error_Handler(void) { __disable_irq(); while (1) {} }
