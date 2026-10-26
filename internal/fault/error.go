// Package fault defines transport-neutral application errors.
package fault

type Error struct {
	Status  int
	Code    string
	Message string
}

func (e *Error) Error() string                    { return e.Message }
func New(status int, code, message string) *Error { return &Error{status, code, message} }
